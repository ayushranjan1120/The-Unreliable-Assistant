// Grafana k6 load test (design §1.11, Gate 15/16). Spec profile: 2m→10K, 3m→50K, 5m sustain, 2m ramp-down.
// Deviations (design §0): SC-08 think time 5 s (THINK_S) for certification; THINK_S=1 is the separate OVERLOAD scenario.
// SC-04: p50 < 240 ms (stricter of 250/240). Query mix from queries.json (5% are the spec's LOT-88392 query) so the
// cache-miss/generation path is exercised, not just one cached query. SCALE divides all VU targets for smaller hosts/rungs.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

const QUERIES = new SharedArray('queries', () => JSON.parse(open('./queries.json')));
const SCALE = Number(__ENV.SCALE || 1);
const THINK_S = Number(__ENV.THINK_S || 5);
const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const T = (n) => Math.max(1, Math.round(n / SCALE));
const PROFILES = {
  L2: [{ duration: '1m', target: T(5000) }, { duration: '10m', target: T(5000) }, { duration: '1m', target: 0 }],
  L3: [{ duration: '2m', target: T(10000) }, { duration: '10m', target: T(10000) }, { duration: '1m', target: 0 }],
  L4: [{ duration: '2m', target: T(25000) }, { duration: '10m', target: T(25000) }, { duration: '1m', target: 0 }],
  L5: [{ duration: '2m', target: T(10000) }, { duration: '3m', target: T(50000) }, { duration: '5m', target: T(50000) }, { duration: '2m', target: 0 }],
  SMOKE: [{ duration: '10s', target: T(100) }, { duration: '30s', target: T(100) }, { duration: '5s', target: 0 }],
};
export const options = {
  stages: PROFILES[__ENV.RUNG || 'L5'],
  thresholds: {
    http_req_duration: ['p(50)<240', 'p(95)<450', 'p(99)<600',
      { threshold: 'p(99)<1500', abortOnFail: true, delayAbortEval: '60s' }],       // abort condition (design §1.11)
    http_req_failed: ['rate<0.0005', { threshold: 'rate<0.01', abortOnFail: true, delayAbortEval: '60s' }],
    checks: ['rate>0.999'],
  },
  summaryTrendStats: ['min', 'avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const vuId = __VU;
  const tenant = `tenant_${vuId % 50}`;
  const payload = JSON.stringify({ tenant_id: tenant, user_id: `user_${vuId}`, query: QUERIES[(vuId * 7 + __ITER) % QUERIES.length] });
  const params = { headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': tenant } };
  const res = http.post(`${BASE}/api/v1/agent/query`, payload, params);
  check(res, {
    'status is 200': (r) => r.status === 200,
    'latency within 200-600ms SLA': (r) => r.timings.duration <= 600,
    'no cross-tenant leak': (r) => r.status === 200 && r.json('tenant_id') === tenant,
  });
  sleep(THINK_S);
}
