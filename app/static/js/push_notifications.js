(async () => {
  const push = window.Capacitor?.Plugins?.PushNotifications;
  if (!push) return;
  const csrf = document.querySelector('input[name="csrf"]')?.value;
  if (!csrf) return;
  try {
    await push.createChannel({ id: 'vendas_som_v2', name: 'Novas vendas', description: 'Avisos sonoros de novos pedidos', importance: 5, visibility: 1, vibration: true });
    await push.createChannel({ id: 'mensagens_som_v1', name: 'Novas mensagens', description: 'Avisos sonoros de mensagens recebidas', importance: 5, visibility: 1, vibration: true });
    let permission = await push.checkPermissions();
    if (permission.receive === 'prompt') permission = await push.requestPermissions();
    if (permission.receive !== 'granted') return;
    await push.addListener('registration', async ({ value }) => {
      await fetch('/api/notificacoes/dispositivos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csrf, token: value }) });
    });
    await push.addListener('pushNotificationActionPerformed', event => {
      window.location.href = event.notification?.data?.url || '/vendas';
    });
    await push.register();
  } catch (error) {
    console.warn('Não foi possível ativar as notificações.', error);
  }
})();
