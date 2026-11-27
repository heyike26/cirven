"""Original, trusted, pure formatter fixture. No files, network or shell."""
def initialize():
    return {"provider": "eeas.fixture.formatter", "version": "1", "capabilities": ["text.format"]}


def invoke(text):
    return "v1|" + text
