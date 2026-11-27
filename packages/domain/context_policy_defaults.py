import json
from packages.domain.context_policy_models import DEFAULT_POLICY
from packages.domain.store import Conflict
from packages.domain.tasks import canonical


class ContextPolicyDefaults:
    @staticmethod
    def _global_defaults(db):
        row = db.execute('SELECT * FROM context_policy_defaults WHERE singleton=1').fetchone()
        if row is None:
            raise Conflict('Global context defaults record is missing')
        value = dict(row)
        value['policy'] = json.loads(value.pop('policy_json'))
        value['effective_policy'] = {**DEFAULT_POLICY, **value['policy']}
        return value

    def global_defaults(self):
        with self.store.connect() as db:
            return {**self._global_defaults(db), 'source_contents_shared': False, 'execution_authorized': False}

    def save_global_defaults(self, request):
        from packages.domain.context_graph import stamp
        def apply(db):
            previous = self._global_defaults(db)
            if previous['revision'] != request.expected_revision:
                raise Conflict('Global context defaults changed; preserve the current settings')
            revision = previous['revision'] + 1
            db.execute('UPDATE context_policy_defaults SET policy_json=?,revision=?,updated_at=? WHERE singleton=1',
                (canonical(request.policy.model_dump(exclude_none=True)), revision, stamp()))
            return {'defaults_revision': revision, 'source_contents_shared': False, 'execution_authorized': False}
        return self._mutate('global_defaults', request, apply)
