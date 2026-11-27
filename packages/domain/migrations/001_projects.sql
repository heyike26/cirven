CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
