from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from email.parser import BytesParser
from email.policy import default
import re
from packages.domain.store import InvalidInput, validate_document_name
from packages.domain.document_import import MAX_DOCUMENT_BYTES


class BodyLimitMiddleware:
    """Bound actual streamed bytes, including requests without Content-Length."""
    def __init__(self, app, limit=2 * 1024 * 1024):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages = []
        size = 0
        document_upload = scope.get("method") in {"POST", "PUT"} and re.fullmatch(
            r"/api/projects/[^/]+/documents(?:/[^/]+)?", scope.get("path", ""))
        stage_template = scope.get("method") == "POST" and scope.get("path") == "/api/stage-workflows/template"
        limit = MAX_DOCUMENT_BYTES + 1024 * 1024 if document_upload or stage_template else self.limit
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise ClientDisconnect
            size += len(message.get("body", b""))
            if size > limit:
                response = JSONResponse({"error": {"code": "BODY_TOO_LARGE", "message": f"Request exceeds {limit // (1024 * 1024)} MiB"}}, status_code=413)
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
        if content_type.lower().startswith(b"multipart/form-data"):
            original = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + content_type + b"\r\nMIME-Version: 1.0\r\n\r\n" +
                b"".join(m.get("body", b"") for m in messages))
            # Multipart libraries normalize legacy Windows paths. Validate before normalization.
            for part in original.walk():
                filename = part.get_filename()
                if filename is not None:
                    try:
                        if stage_template:
                            # The stage parser supports additional text and spreadsheet formats.
                            # Apply the same raw filename checks before multipart normalization.
                            if not filename or len(filename) > 200 or any(c in filename for c in ("/", "\\", ":", "\x00")):
                                raise InvalidInput("Template requires a plain filename")
                        elif scope.get("path") == "/api/extensions":
                            if not filename.lower().endswith(".zip"):
                                raise InvalidInput("Only local ZIP extension archives are supported")
                            # Reuse the unchanged raw path/drive/null guards, while
                            # retaining the document-only suffix policy elsewhere.
                            validate_document_name(filename[:-4] + ".md")
                        else:
                            validate_document_name(filename)
                    except InvalidInput as exception:
                        response = JSONResponse({"error": {"code": "INVALID_INPUT", "message": str(exception)}}, status_code=422)
                        return await response(scope, receive, send)
        iterator = iter(messages)

        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()
        return await self.app(scope, replay, send)
