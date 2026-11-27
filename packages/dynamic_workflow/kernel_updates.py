"""User-triggered updates from the native packages published by each kernel."""
import base64
import hashlib
import hmac
import json
import os
import platform
import re
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from packages.dynamic_workflow import kernel_settings as settings
from packages.domain.store import Conflict, InvalidInput

REGISTRY="https://registry.npmjs.org"
LIMIT=768*1024**2


def _metadata(url):
    try:
        with httpx.stream("GET",url,timeout=12,follow_redirects=False) as response:
            response.raise_for_status()
            raw=bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw)>2*1024**2:raise InvalidInput("官方版本清单超过读取上限")
        return json.loads(raw)
    except (httpx.HTTPError,ValueError) as error:
        raise InvalidInput("无法读取官方版本清单，可从本机文件更新") from error


def release(engine,version="latest"):
    settings.engine_name(engine)
    if not re.fullmatch(r"latest|[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?",version):
        raise InvalidInput("请输入有效的官方版本号")
    arch={"amd64":"x64","x86_64":"x64","aarch64":"arm64","arm64":"arm64"}.get(platform.machine().lower())
    if not arch:raise InvalidInput("当前架构需要使用本机文件更新")
    if platform.system() not in ("Linux","Windows","Darwin"):raise InvalidInput("当前平台需要使用本机文件更新")
    system={"Windows":"win32" if engine=="claude-code" else "windows","Linux":"linux","Darwin":"darwin"}[platform.system()]
    base="@anthropic-ai/claude-code" if engine=="claude-code" else "opencode-ai"
    package=f"{'@anthropic-ai/claude-code' if engine=='claude-code' else 'opencode'}-{system}-{arch}"
    meta=_metadata(f"{REGISTRY}/{quote(base,safe='@')}/{version}")
    tag=meta.get("version","")
    if meta.get("name")!=base or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?",tag):
        raise InvalidInput("官方主包版本清单无效")
    if meta.get("optionalDependencies",{}).get(package)!=tag:
        raise InvalidInput("此版本未声明适用于当前平台的原厂内核包，可使用本机文件更新")
    native=_metadata(f"{REGISTRY}/{quote(package,safe='@')}/{tag}")
    dist=native.get("dist",{})
    href=dist.get("tarball","")
    url=urlsplit(href)
    integrity=dist.get("integrity","")
    if (native.get("name")!=package or native.get("version")!=tag or url.scheme!="https" or
            url.netloc!="registry.npmjs.org" or url.query or url.fragment or
            not url.path.startswith("/"+package+"/-/") or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==",integrity)):
        raise InvalidInput("原厂平台包的身份、下载来源或完整性摘要无效")
    return dict(version=tag,package=package,url=href,integrity=integrity)


def extract_native(archive,dst,engine):
    binary=("claude" if engine=="claude-code" else "opencode")+(".exe" if os.name=="nt" else "")
    names={"package/"+binary,"package/bin/"+binary}
    found=False
    sha=hashlib.sha256()
    with tarfile.open(archive,"r|gz") as pkg:
        for i,entry in enumerate(pkg):
            if i>=64:raise InvalidInput("平台归档包含过多条目")
            if entry.name not in names:continue
            if found or not entry.isreg() or not 0<entry.size<=LIMIT:raise InvalidInput("平台归档中的内核文件无效")
            found=True
            with pkg.extractfile(entry) as matched,dst.open("xb") as out:
                for data in iter(lambda:matched.read(1024**2),b""):
                    out.write(data);sha.update(data)
    if not found:raise InvalidInput("官方平台包缺少可执行内核")
    return sha.hexdigest()


def download(data_dir,engine,revision,version,integrity):
    info=release(engine,version)
    if info["integrity"]!=integrity:raise Conflict("官方版本清单已改变，请重新检查")
    root=settings.home(data_dir)
    root.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=root) as name:
            archive=Path(name)/"release.tgz"
            sha=hashlib.sha512()
            with httpx.stream("GET",info["url"],timeout=60,follow_redirects=False) as response,archive.open("xb") as out:
                response.raise_for_status()
                size=0
                for block in response.iter_bytes(256*1024):
                    size+=len(block)
                    if size>LIMIT:raise InvalidInput("下载的内核包超过大小上限")
                    sha.update(block);out.write(block)
            if not hmac.compare_digest("sha512-"+base64.b64encode(sha.digest()).decode(),integrity):
                raise InvalidInput("下载的内核包完整性不符")
            binary=Path(name)/"native"
            digest=extract_native(archive,binary,engine)
            settings.install(data_dir,engine,revision,str(binary),digest,version)
    except (OSError,httpx.HTTPError,tarfile.TarError,EOFError) as exc:
        raise InvalidInput("下载或解包未完成，可重试或使用本机文件") from exc
