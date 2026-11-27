"""Original v2 adds an observable format and Unicode character count."""
def initialize():
    return {"provider": "eeas.fixture.formatter", "version": "2", "capabilities": ["text.format", "text.length"]}


def invoke(text):
    return {"format": "v2", "text": text, "length": len(text)}
