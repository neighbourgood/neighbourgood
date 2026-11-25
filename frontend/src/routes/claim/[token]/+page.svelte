<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { token } from '$lib/stores/auth';
	import { t } from 'svelte-i18n';

	// Link sent by the WhatsApp bot (command "account") to a WhatsApp-only member
	const claimToken = $derived($page.params.token ?? '');

	let displayName = $state('');
	let invalid = $state(false);
	let email = $state('');
	let password = $state('');
	let confirm = $state('');
	let error = $state('');
	let loading = $state(false);

	onMount(async () => {
		try {
			const info = await api<{ display_name: string }>(`/auth/claim/${claimToken}`);
			displayName = info.display_name;
		} catch {
			invalid = true;
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		if (password !== confirm) {
			error = $t('auth.passwords_no_match');
			return;
		}
		loading = true;
		try {
			const res = await api<{ access_token: string }>(`/auth/claim/${claimToken}`, {
				method: 'POST',
				body: { email, password }
			});
			token.set(res.access_token);
			goto('/dashboard');
		} catch (err) {
			error = err instanceof Error ? err.message : $t('common.error');
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>{$t('whatsapp.claim_title')} - NeighbourGood</title>
	<!-- The claim token is in the URL: never leak it through the Referer header. -->
	<meta name="referrer" content="no-referrer" />
</svelte:head>

<div class="card auth-card">
	<h1>{$t('whatsapp.claim_title')}</h1>

	{#if invalid}
		<p class="alert alert-error" role="alert">{$t('whatsapp.claim_invalid')}</p>
	{:else if displayName}
		<p class="subtitle">{$t('whatsapp.claim_desc', { values: { name: displayName } })}</p>

		{#if error}
			<p class="alert alert-error" role="alert">{error}</p>
		{/if}

		<form class="form-stack" onsubmit={handleSubmit}>
			<div class="field">
				<label for="claim-email">{$t('auth.email')}</label>
				<input id="claim-email" type="email" bind:value={email} required maxlength="255" autocomplete="email" disabled={loading} />
			</div>
			<div class="field">
				<label for="claim-password">{$t('auth.password')}</label>
				<input
					id="claim-password"
					type="password"
					bind:value={password}
					required
					minlength="8"
					maxlength="128"
					autocomplete="new-password"
					aria-describedby="claim-password-hint"
					disabled={loading}
				/>
				<small id="claim-password-hint" class="field-hint">{$t('auth.password_hint')}</small>
			</div>
			<div class="field">
				<label for="claim-confirm">{$t('auth.reset_confirm_password')}</label>
				<input id="claim-confirm" type="password" bind:value={confirm} required minlength="8" maxlength="128" autocomplete="new-password" disabled={loading} />
			</div>
			<button type="submit" class="btn btn-primary btn-block" class:is-loading={loading} disabled={loading}>
				{$t('whatsapp.claim_submit')}
			</button>
		</form>
	{/if}
</div>
