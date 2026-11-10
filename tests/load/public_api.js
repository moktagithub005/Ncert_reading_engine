import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const baseURL = __ENV.BASE_URL || 'http://localhost:8000';
const cachedDuration = new Trend('cached_duration', true);

export const options = {
  vus: 200,
  duration: '30s',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<150'],
    cached_duration: ['p(95)<30'],
  },
};

export default function () {
  const url = `${baseURL}/api/v1/classes`;
  const first = http.get(url);
  check(first, {
    'class list is available': (response) => response.status === 200,
    'class list is cacheable': (response) => response.headers['Cache-Control']?.includes('max-age=3600'),
  });

  const etag = first.headers.ETag;
  if (etag) {
    const cached = http.get(url, { headers: { 'If-None-Match': etag } });
    cachedDuration.add(cached.timings.duration);
    check(cached, { 'conditional class list returns 304': (response) => response.status === 304 });
  }
}
