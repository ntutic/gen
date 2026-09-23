// Per-app fetcher (duplicated in src/web/api/client.ts on purpose:
// strict app isolation, no cross-imports). Cache is disabled so every query
// refetch hits the backend; TanStack Query owns all caching instead.
export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', ...options })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.detail || `Request failed (${response.status})`)
  }
  return response.json()
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}
