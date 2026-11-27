# Original model request authority

An application-owned OpenCode plugin using the pinned 733b2c6 `chat.params`
hook. Before request construction completes, it sends only exact owner, root,
native-session, original user-message, generation, agent and step identities
to an authenticated loopback callback in the existing host. No prompt body,
source body, model parameter or credential is projected to UI or another model.

The callback does not reserve a paid model call, start another owner or change
the model. The existing API relay remains authoritative for its own counters.
The hook is one native request-plan admission, not proof that bytes were sent,
that an SDK internal retry rechecked authority, or that an old disclosure was
erased. Mid-flight upstream work cannot be recalled by this check.

Source only: native SDK, subscription, concurrency, shutdown and Windows
qualification remain NOT_RUN until concentrated debug and complete acceptance.


Subscription HTTP reservations (source connected, runtime qualification pending):
The exact original account owner may select the pinned Codex HTTP fallback with
its existing internal transport hint. Original auth retains login, refresh and
Authorization. The process-local final fetch wrapper reserves one durable host
allowance for every HTTP attempt including original runtime retries, strips its
private marker, refuses redirects, and forwards the original payload once. No
credentials or prompt body enter the broker. No new model loop, paid API fallback,
WebSocket accounting, commercial usage or output-token enforcement is claimed.
API profiles keep the prior metadata hook and ProviderWire accounting.


Adapter executions use ADAPTER_EXECUTION_V1 with their own execution_ref,
immutable agent_executions/agent_scopes and original planning/desktop source
checks. They never synthesize an AgentSessions owner. The same packaged final
HTTP boundary retains its namespace through plan and hop receipts. Source-only;
actual three-runtime transport and owned shutdown qualification are pending.

The final HTTP hop also sends the original UTF-8 request byte count (bounded to
4 MiB). This is a cumulative software input-payload charge, not physical I/O or
provider token usage. Legacy reference-only receipts keep their original digest
when this optional field is absent; newly packaged producers include it.
