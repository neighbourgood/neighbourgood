<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from 'svelte-i18n';
  import { api } from '$lib/api';
  import type { CommunityWhatsApp, WhatsAppLinkStart } from '$lib/types';
  import Icon from '$lib/components/Icon.svelte';

  let { communityId }: { communityId: number } = $props();

  let status = $state<CommunityWhatsApp | null>(null);
  let linkMessage = $state('');
  let busy = $state(false);
  let error = $state('');
  let copied = $state(false);

  onMount(load);

  async function load() {
    try {
      status = await api<CommunityWhatsApp>(`/communities/${communityId}/whatsapp`, { auth: true });
    } catch {
      status = null;
    }
  }

  async function run(fn: () => Promise<void>) {
    busy = true;
    error = '';
    try {
      await fn();
    } catch (err) {
      error = err instanceof Error ? err.message : $t('whatsapp.failed');
    } finally {
      busy = false;
    }
  }

  const createCode = () =>
    run(async () => {
      const res = await api<WhatsAppLinkStart>(`/communities/${communityId}/whatsapp/start-link`, {
        method: 'POST',
        auth: true
      });
      linkMessage = res.message;
    });

  const setDigest = (digest: string) =>
    run(async () => {
      status = await api<CommunityWhatsApp>(`/communities/${communityId}/whatsapp`, {
        method: 'PATCH',
        body: { digest },
        auth: true
      });
    });

  const unlink = () =>
    run(async () => {
      await api(`/communities/${communityId}/whatsapp`, { method: 'DELETE', auth: true });
      linkMessage = '';
      await load();
    });

  function copy() {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(linkMessage).then(() => {
        copied = true;
        setTimeout(() => (copied = false), 2000);
      });
    } else {
      window.prompt($t('whatsapp.copy'), linkMessage);
    }
  }
</script>

{#if status?.configured}
<section class="card whatsapp-section slide-up">
  <div class="section-header">
    <h2>{$t('whatsapp.group_title')}</h2>
    {#if status.linked}
      <span class="badge badge-success"><Icon name="check" size={13} />{$t('whatsapp.group_linked')}</span>
    {/if}
  </div>
  <p class="section-hint">{$t('whatsapp.group_desc')}</p>

  {#if error}
    <div class="alert alert-error" role="alert">{error}</div>
  {/if}

  {#if status.linked}
    <label class="field">
      <span>{$t('whatsapp.digest')}</span>
      <select value={status.digest} onchange={(e) => setDigest((e.currentTarget as HTMLSelectElement).value)} disabled={busy}>
        <option value="weekly">{$t('whatsapp.digest_weekly')}</option>
        <option value="daily">{$t('whatsapp.digest_daily')}</option>
        <option value="off">{$t('whatsapp.digest_off')}</option>
      </select>
    </label>
    <p class="info-hint">{$t('whatsapp.digest_hint')}</p>
    <button class="btn btn-danger-outline btn-sm" onclick={unlink} disabled={busy}>{$t('whatsapp.group_unlink')}</button>
  {:else if linkMessage}
    <ol class="steps">
      <li>{$t('whatsapp.group_step_add')}</li>
      <li>
        {$t('whatsapp.group_step_post')}
        <div class="code-row">
          <code>{linkMessage}</code>
          <button class="btn btn-secondary btn-sm" onclick={copy}>{copied ? $t('whatsapp.copied') : $t('whatsapp.copy')}</button>
        </div>
      </li>
    </ol>
    <button class="btn btn-secondary btn-sm" onclick={load}>{$t('whatsapp.group_check')}</button>
  {:else}
    <button class="btn btn-primary" class:is-loading={busy} onclick={createCode} disabled={busy}>{$t('whatsapp.group_create_code')}</button>
  {/if}
</section>
{/if}

<style>
  .whatsapp-section {
    margin-bottom: 1rem;
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
  }
  .section-header h2 {
    font-size: 1.05rem;
    font-weight: 500;
    margin: 0;
  }
  .section-hint,
  .info-hint {
    color: var(--color-text-muted);
    font-size: 0.9rem;
  }
  .steps {
    padding-inline-start: 1.25rem;
    margin: 0 0 1rem;
    display: grid;
    gap: 0.5rem;
  }
  .code-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.35rem;
  }
  code {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm, 6px);
    padding: 0.25rem 0.5rem;
    font-size: 1rem;
    user-select: all;
  }
  .field {
    display: grid;
    gap: 0.35rem;
    max-width: 16rem;
    margin-bottom: 0.25rem;
  }
</style>
