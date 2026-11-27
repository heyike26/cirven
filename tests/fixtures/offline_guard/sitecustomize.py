"""Original Python-network guard for the offline install probe; not an OS sandbox."""
import ipaddress
import socket

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_getaddrinfo = socket.getaddrinfo


def allowed(host):
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def getaddrinfo(host, *args, **kwargs):
    if not allowed(host):
        raise RuntimeError("Offline probe blocked non-loopback DNS")
    return original_getaddrinfo(host, *args, **kwargs)


def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not allowed(address[0]):
        raise RuntimeError("Offline probe blocked non-loopback connection")
    return original_connect(self, address)


def connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not allowed(address[0]):
        raise RuntimeError("Offline probe blocked non-loopback connection")
    return original_connect_ex(self, address)


socket.getaddrinfo = getaddrinfo
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
