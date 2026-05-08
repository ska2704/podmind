# guest-sim

Tiny traffic generator. Hits the gateway with synthetic logins,
bookings, and check-out floods so the dependency graph isn't quiet
between chaos experiments.

It does **not** generate sensor data — that's sensor-ingest's job, via
its internal ticker.

## Tuning

Edit `guest-sim-knobs` ConfigMap:

| key                       | default | meaning                          |
|---------------------------|---------|----------------------------------|
| `GATEWAY_URL`             | http://gateway.sh-core | target |
| `LOGIN_RATE_PER_MIN`      | 20      | steady-state login rate          |
| `BOOKING_LAMBDA_PER_MIN`  | 5       | Poisson lambda for bookings      |
| `CHECKOUT_FLOOD_INTERVAL_S` | 60    | period of check-out spike        |

After editing the ConfigMap:

```
kubectl rollout restart deployment/guest-sim -n sh-core
```

## Silence

```
kubectl scale deployment/guest-sim -n sh-core --replicas=0
```
