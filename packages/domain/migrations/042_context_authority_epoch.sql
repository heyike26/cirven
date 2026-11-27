-- Invalidate only cached current-authority checks, never historical receipts.
CREATE TABLE context_authority_epoch (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    revision INTEGER NOT NULL CHECK(revision>=1)
);
INSERT INTO context_authority_epoch(singleton,revision) VALUES(1,1);
CREATE TRIGGER context_document_authority_changed
AFTER UPDATE OF current_revision,deleted_at ON documents
WHEN OLD.current_revision IS NOT NEW.current_revision OR OLD.deleted_at IS NOT NEW.deleted_at
BEGIN
    UPDATE context_authority_epoch SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER context_document_authority_removed AFTER DELETE ON documents
BEGIN
    UPDATE context_authority_epoch SET revision=revision+1 WHERE singleton=1;
END;
