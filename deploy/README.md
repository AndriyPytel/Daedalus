# Home-server stack

Postgres, the gateway and a worker-host on one Podman host, from `deploy/compose.yaml`. Only the gateway publishes a port: `8505` on `127.0.0.1` and on the Tailscale address `100.90.228.80`, so the LAN address does not answer.

- `postgres`: Postgres 17, data in the volume `pgdata`.
- `gateway`: FastAPI under uvicorn, built from `deploy/gateway/Containerfile` with no FreeCAD. It applies the migrations at start, keeps blobs in the volume `blobs` and runs every Session's worker as `python -m service.relay worker-host:7000`.
- `worker-host`: the `deploy/worker/Containerfile` image (FreeCAD 1.1.4, `sdk.Client`, `claude` 2.1.289 pinned by sha256). `python -m service.workerhost` listens on 7000 inside the compose network only and runs one `service.worker` process group per connection, killed when the connection closes.

The server defaults select the provider `Claude (subscription)` with the alias `haiku` in every role; a Run, or the owner's preferences, can name `sonnet` or `opus`.

## Secrets

The values never enter a shell history or a file: `ssh -t` gives a terminal, `read -s` takes the value without echo, and `podman secret create NAME -` reads it from the pipe. Run each pair on the server, then paste the value and press Enter.

    ssh -t andrii@192.168.0.150
    read -rs V && printf %s "$V" | podman secret create daedalus-token - ; unset V
    read -rs V && printf %s "$V" | podman secret create daedalus-claude-token - ; unset V

- `daedalus-token` is the bearer token clients send. Generate it on the Mac into the password manager (`openssl rand -hex 32`) and paste it.
- `daedalus-claude-token` is the output of `claude setup-token` on the Mac. It reaches `worker-host` as `CLAUDE_CODE_OAUTH_TOKEN`; the stack never uses `ANTHROPIC_API_KEY`.

`podman secret ls` lists the names. `podman secret rm NAME` removes one, and the container must be recreated to pick up a new value.

## Up, down, logs

From the repository on the server:

    podman compose -f deploy/compose.yaml up -d --build
    podman compose -f deploy/compose.yaml ps
    podman compose -f deploy/compose.yaml logs -f gateway
    podman compose -f deploy/compose.yaml down

`down` keeps the volumes, so Sessions and Runs survive a `down` and `up`; `down -v` deletes them. The first build downloads FreeCAD and takes several minutes.

From the Mac, with Tailscale on:

    curl http://100.90.228.80:8505/v1/health
    curl -H "Authorization: Bearer $TOKEN" http://100.90.228.80:8505/v1/sessions

`DAEDALUS_PORT` changes the published port. A request without the token is `401`.
