/**
 * Server hook to proxy /api requests to the backend.
 * In development, Vite handles this. In production (Docker),
 * this hook forwards requests to the backend service.
 */

import type { Handle } from '@sveltejs/kit';

const API_BACKEND = process.env.API_BACKEND || 'http://localhost:8300';

function backendUnavailable(): Response {
	return new Response(JSON.stringify({ detail: 'Backend unavailable — please try again.' }), {
		status: 502,
		headers: { 'Content-Type': 'application/json' }
	});
}

export const handle: Handle = async ({ event, resolve }) => {
	if (event.url.pathname.startsWith('/api/') || event.url.pathname === '/api') {
		const backendPath = event.url.pathname.replace(/^\/api/, '');
		const backendUrl = `${API_BACKEND}${backendPath}${event.url.search}`;

		const headers = new Headers(event.request.headers);
		headers.delete('host');
		// Forward the real client IP so the backend rate-limiter buckets per user,
		// not per proxy. getClientAddress() returns the connecting client's IP as
		// seen by the Node adapter (respects adapter-level trusted proxies).
		headers.set('x-forwarded-for', event.getClientAddress());

		const init: RequestInit = {
			method: event.request.method,
			headers,
			body: event.request.method !== 'GET' && event.request.method !== 'HEAD'
				? await event.request.arrayBuffer()
				: undefined,
			// @ts-expect-error duplex needed for streaming bodies
			duplex: 'half'
		};

		// A transient connection failure (backend restarting, or a keep-alive
		// socket the backend already closed) would otherwise bubble up as an
		// opaque 500. Retry idempotent requests once on a fresh connection;
		// everything else gets a clean 502 the frontend can display.
		let response: Response;
		try {
			response = await fetch(backendUrl, init);
		} catch (err) {
			if (event.request.method === 'GET' || event.request.method === 'HEAD') {
				try {
					response = await fetch(backendUrl, init);
				} catch {
					return backendUnavailable();
				}
			} else {
				console.error('API proxy request failed:', err);
				return backendUnavailable();
			}
		}

		return new Response(response.body, {
			status: response.status,
			statusText: response.statusText,
			headers: response.headers
		});
	}

	return resolve(event);
};
