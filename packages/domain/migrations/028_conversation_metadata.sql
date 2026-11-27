ALTER TABLE conversation_threads ADD COLUMN display_title TEXT;
ALTER TABLE conversation_threads ADD COLUMN activity_at TEXT;
UPDATE conversation_threads SET activity_at=created_at WHERE activity_at IS NULL;
