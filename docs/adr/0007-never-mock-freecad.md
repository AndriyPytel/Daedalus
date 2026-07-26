# FreeCAD is never mocked in tests

Mocking FreeCAD tests our belief about the FreeCAD API — and the whole problem this project fights is that belief drifting from reality. `addTodo` that does not exist, `Support` and `Plane` that were removed, `AttachmentSupport` that nobody knew about: a mocked test passes on every one of them and certifies the mistake. There is also physical evidence in this repo's history, a `MagicMock/mock.getUserAppDataDir().__add__()/…` directory tree that a stringified mock wrote to disk.

Tests are split by what they actually need instead:

- Everything that does not touch FreeCAD — the loop, its budgets and exit conditions, the event stream, the lint rules, provider adaptation — is plain pytest against a fake Tool registry, and is where TDD happens. All Document access goes through Tools, so this covers the whole agent loop.
- Anything that touches a Document runs headless in a real FreeCAD, reusing the Preflight Worker. The test harness is a part of the product, not scaffolding around it.
- The Qt panel is checked by hand. Automating it does not pay for itself.
