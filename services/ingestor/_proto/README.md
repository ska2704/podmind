# Vendored Hubble proto

`observer.proto` is a deliberately minimal subset of Hubble's upstream
proto tree. It carries only the fields the ingestor needs:

- `Observer.GetFlows` (server-streaming)
- `Flow` time, verdict, source endpoint, destination endpoint, L4
- `Endpoint` id, namespace, pod_name
- `Layer4` TCP/UDP with source and destination ports

Field numbers match upstream so the wire format is compatible. When
upstream adds something the agents need (e.g. drop reasons), copy the
field in and re-run `make protos`.

## Regenerating the stubs

```
make protos
```

That runs `python -m grpc_tools.protoc` against this directory and
writes `observer_pb2.py` and `observer_pb2_grpc.py` into
`services/ingestor/app/_proto/`. The generated files are committed —
`grpcio-tools` is a dev dep, not a runtime dep.
