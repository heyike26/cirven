"""Storage accident guards only; not an API attack or privileged-DB security proof."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def database(path):
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys=ON')
    scripts = sorted((ROOT/'packages/domain/migrations').glob('*.sql'))
    # Same original SQL in one real transaction, like Store.migrate; avoid
    # expensive per-statement autocommit fsyncs in the independent SQL fixture.
    connection.executescript('BEGIN IMMEDIATE;\n' + '\n'.join(p.read_text() for p in scripts) + '\nCOMMIT;')
    for project in ('original-project', 'other-project'):
        connection.execute('INSERT INTO projects VALUES (?,?,?,?)', (project, project, '', '2026-10-13'))
    for qid, pid in (('original-question', 'original-project'), ('other-question', 'other-project')):
        connection.execute('INSERT INTO project_questions(id,project_id,current_revision) VALUES (?,?,?)', (qid, pid, 1))
        # Opaque original SQL storage input, not an API-admitted Question.
        payload = json.dumps({'source_kind': 'synthetic', 'project_id': pid, 'question_id': qid,
                              'original_text': '原创结构存储夹具。'}, ensure_ascii=False)
        connection.execute('INSERT INTO project_question_versions VALUES (?,?,?,?,?,?)',
            (qid, 1, payload, hashlib.sha256(payload.encode()).hexdigest(), qid, '2026-10-13'))
    connection.execute('INSERT INTO project_answers(id,question_id,current_revision) VALUES (?,?,?)',
        ('original-answer', 'original-question', 1))
    payload = '{"source_kind":"synthetic","original_text":"Original answer storage fixture"}'
    connection.execute('INSERT INTO project_answer_versions VALUES (?,?,?,?,?)',
        ('original-answer', 1, payload, hashlib.sha256(payload.encode()).hexdigest(), '2026-10-13'))
    connection.commit()
    return connection


class PersistentScopeIdentity(unittest.TestCase):
    def test_parent_identity_project_and_question_scope_are_immutable(self):
        statements = [
            "UPDATE project_questions SET project_id='other-project' WHERE id='original-question'",
            "UPDATE project_questions SET id='changed-question' WHERE id='original-question'",
            "UPDATE project_questions SET seq=99999 WHERE id='original-question'",
            "DELETE FROM project_questions WHERE id='original-question'",
            "UPDATE project_answers SET question_id='other-question' WHERE id='original-answer'",
            "UPDATE project_answers SET id='changed-answer' WHERE id='original-answer'",
            "UPDATE project_answers SET seq=99999 WHERE id='original-answer'",
            "DELETE FROM project_answers WHERE id='original-answer'",
        ]
        observations = []
        for statement in statements:
            with self.subTest(statement=statement), tempfile.TemporaryDirectory() as temp:
                connection = database(Path(temp)/'actual.sqlite3')
                before = [list(row) for row in connection.execute('SELECT id,project_id,seq FROM project_questions ORDER BY seq')]
                error = None
                try:
                    with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                        try:
                            connection.execute(statement)
                            connection.commit()
                        except sqlite3.IntegrityError as exception:
                            error = str(exception)
                            raise
                finally:
                    observations.append({'statement': statement, 'exception': error,
                        'questions_before': before,
                        'questions_after': [list(row) for row in connection.execute('SELECT id,project_id,seq FROM project_questions ORDER BY seq')],
                        'answers_after': [list(row) for row in connection.execute('SELECT id,question_id,seq FROM project_answers ORDER BY seq')]})
                    connection.close()
        if os.environ.get('EEAS_EVIDENCE_DIR'):
            target = Path(os.environ['EEAS_EVIDENCE_DIR'])/'actual-sql-scope-observations.json'
            with target.open('x', encoding='utf-8') as stream:
                json.dump({'schema': 'eeas.original-sql-scope-observation.v1', 'source_kind': 'synthetic',
                    'qualification': 'STORAGE_ACCIDENT_GUARD_ONLY', 'api_attack_proven': False,
                    'privileged_db_tamper_resistance_proven': False, 'cases': observations}, stream, indent=2)
                stream.write('\n')

    def test_append_revision_can_advance_head_without_rewriting_original_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            connection = database(Path(temp)/'actual.sqlite3')
            try:
                old_question = connection.execute('SELECT * FROM project_question_versions WHERE question_id=? AND revision=1',
                                                 ('original-question',)).fetchone()
                old_answer = connection.execute('SELECT * FROM project_answer_versions WHERE answer_id=? AND revision=1',
                                               ('original-answer',)).fetchone()
                connection.execute('INSERT INTO project_question_versions VALUES (?,?,?,?,?,?)',
                    ('original-question', 2, old_question[2], old_question[3], old_question[4], '2026-10-14'))
                connection.execute('UPDATE project_questions SET current_revision=2 WHERE id=?', ('original-question',))
                connection.execute('INSERT INTO project_answer_versions VALUES (?,?,?,?,?)',
                    ('original-answer', 2, old_answer[2], old_answer[3], '2026-10-14'))
                connection.execute('UPDATE project_answers SET current_revision=2 WHERE id=?', ('original-answer',))
                connection.commit()
                self.assertEqual(connection.execute('SELECT * FROM project_question_versions WHERE question_id=? AND revision=1',
                                                    ('original-question',)).fetchone(), old_question)
                self.assertEqual(connection.execute('SELECT * FROM project_answer_versions WHERE answer_id=? AND revision=1',
                                                    ('original-answer',)).fetchone(), old_answer)
                self.assertEqual(connection.execute('SELECT current_revision FROM project_questions WHERE id=?',
                                                    ('original-question',)).fetchone()[0], 2)
                self.assertEqual(connection.execute('SELECT current_revision FROM project_answers WHERE id=?',
                                                    ('original-answer',)).fetchone()[0], 2)
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
