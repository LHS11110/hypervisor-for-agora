# VM workspace files

Build the pinned Automerge helper before `runtime/prepare-guest`, `runtime/install` or `runtime/update-agent`:

```bash
cd runtime/workspace
corepack pnpm@10.32.1 install --frozen-lockfile
corepack pnpm@10.32.1 run build
```

The generated `runtime/guest/workspace-crdt.cjs` includes the Automerge MIT license. It is intentionally excluded from Git. No package manager or network download runs in the guest.

`guest/workspace.py` listens only for host VSOCK connections on port 5003. Each request executes as UID 1000 with bounded input, output, file size and execution time. The existing manager forwards `{canvasId, action: "workspace", request: {...}}` only to that canvas's guest. Browser users authenticate through Wall and the C++ broker, never directly to VSOCK or the manager socket.

Requests use `operation: stat|list|read|write|mkdir|rename|delete` and a canonical absolute workspace-relative `path`. Mutations carry a server-scoped `requestId`. Text writes supply `fileId` and an Automerge `document`; binary writes supply `fileId`, `etag` and base64 `content`. Creates use `create: true` and base64 `content`. Rename/delete use the `resourceId` returned by stat to reject changes to a newer filesystem object. Rename never replaces an existing destination.

State lives in `/var/lib/agora-workspace/files.sqlite` inside the VM disk. Each text replica has a file generation ID; replacing a file through an external atomic rename starts a new generation. SQLite WAL, mutation intents and the most recent 2,048 durable request receipts support retry and restart recovery. External shell writes that keep the same inode become CRDT text updates when next read. Arbitrary outside writers do not participate in a shared filesystem lock.

This feature does not restart running guests to update them. An old running guest returns `WORKSPACE_AGENT_UPGRADE_REQUIRED`; stopped guests receive the worker through the existing agent updater on their next normal boot.

Tests operate on temporary directories, including the real bundled merge worker:

```bash
python3 -m unittest discover -s runtime/tests -p test_workspace.py -v
```

The matching VS Code web extension and complete setup instructions are in `IRADE/agora-workspace/README.md` in the sibling IRADE repository.
