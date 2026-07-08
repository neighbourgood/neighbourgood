/**
 * Universal layout load — initialises svelte-i18n and, crucially, awaits the
 * locale dictionary before any page renders.
 *
 * Without the await, the very first SSR request after a server (re)start
 * rendered while the async locale loader was still in flight and crashed with
 * "[svelte-i18n] Cannot format a message without first setting the initial
 * locale" — a 500 for whoever hit the freshly restarted instance first
 * (typically a returning logged-in user opening the app).
 */

import { waitLocale } from 'svelte-i18n';
import { setupI18n, detectInitialLocale } from '$lib/i18n';
import type { LayoutLoad } from './$types';

let initialised = false;

export const load: LayoutLoad = async () => {
	if (!initialised) {
		// detectInitialLocale safely returns 'en' on the server (no
		// localStorage/navigator) and the visitor's preference in the browser.
		setupI18n(detectInitialLocale());
		initialised = true;
	}
	await waitLocale();
};
