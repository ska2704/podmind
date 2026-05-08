.PHONY: protos up down test test-contracts test-ingestor lint

# Generate Hubble gRPC stubs from the vendored observer.proto.
# Patches the protoc-emitted absolute import to a package-relative one
# so `from app._proto import observer_pb2_grpc` works.
protos:
	uv run --package podmind-ingestor python -m grpc_tools.protoc \
		-Iservices/ingestor/_proto \
		--python_out=services/ingestor/app/_proto \
		--grpc_python_out=services/ingestor/app/_proto \
		services/ingestor/_proto/observer.proto
	uv run python -c "import re, pathlib as p; f = p.Path('services/ingestor/app/_proto/observer_pb2_grpc.py'); f.write_text(re.sub(r'^import observer_pb2', 'from . import observer_pb2', f.read_text(), flags=re.M))"

up:
	bash scripts/dev-up.sh

# Each delete uses --ignore-not-found so a partial earlier teardown
# still tears the rest down. K3s itself is left running — use
# k3s-uninstall.sh if you actually want the node gone.
down:
	kubectl delete -k infra/smarthostel/ --ignore-not-found=true
	kubectl delete -k infra/guest-sim/   --ignore-not-found=true
	kubectl delete -k infra/ingestor/    --ignore-not-found=true

test: test-contracts test-ingestor

test-contracts:
	uv run --package podmind-contracts pytest services/contracts/tests/

test-ingestor:
	uv run --package podmind-ingestor pytest services/ingestor/tests/

lint:
	uv run ruff check services/
