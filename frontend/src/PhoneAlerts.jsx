import { useEffect, useState } from 'react';
import { wsApi } from './workspace-ui.jsx';

export async function disablePhoneAlerts() {
  if (!('serviceWorker' in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return;
  try { await wsApi('/push/unsubscribe', { method: 'POST', body: { endpoint: subscription.endpoint } }); }
  finally { await subscription.unsubscribe(); }
}

export default function PhoneAlerts({ me }) {
  const [open, setOpen] = useState(false), [enabled, setEnabled] = useState(false), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  useEffect(() => {
    let active = true;
    if (supported) navigator.serviceWorker.getRegistration('/').then(r => r?.pushManager.getSubscription()).then(async sub => {
      if (!sub) return;
      const state = await wsApi('/push/status', { method:'POST', body:{endpoint:sub.endpoint} });
      if (active) setEnabled(state.enabled);
    }).catch(() => {});
    return () => { active = false; };
  }, [me.id, supported]);
  const enable = async () => {
    setBusy(true); setMessage('');
    try {
      // Permission is requested only from this explicit user gesture.
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Notifications are blocked. Allow notifications for Mill City in your phone or browser settings.');
      const registration = await navigator.serviceWorker.register('/push-worker.js');
      await navigator.serviceWorker.ready;
      const { public_key } = await wsApi('/push/config');
      let sub = await registration.pushManager.getSubscription();
      if (sub) { await sub.unsubscribe(); sub = null; }
      const key = Uint8Array.from(atob(public_key.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-public_key.length%4)%4)), c=>c.charCodeAt(0));
      sub = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:key});
      try { await wsApi('/push/subscribe', {method:'POST', body:sub.toJSON()}); }
      catch (e) { await sub.unsubscribe(); throw e; }
      setEnabled(true); setMessage('Phone alerts enabled for new website inquiries. Send a test to check delivery.');
    } catch (e) { setMessage(e.message); } finally { setBusy(false); }
  };
  const disable = async () => { setBusy(true); try { await disablePhoneAlerts(); setEnabled(false); setMessage('Phone alerts turned off on this device.'); } catch(e) { setMessage(e.message); } finally { setBusy(false); } };
  const test = async () => { setBusy(true); try {
    const r = await navigator.serviceWorker.getRegistration('/'); const sub = await r?.pushManager.getSubscription();
    if (!sub) throw new Error('Enable phone alerts first.');
    await wsApi('/push/test', {method:'POST',body:{endpoint:sub.endpoint}});
    setMessage('Test sent. Check your notifications. Focus or Do Not Disturb may silence it.');
  } catch(e) { setMessage(e.message); } finally { setBusy(false); } };
  return <div className="ws-phone-alerts"><button className="secondary" onClick={()=>setOpen(!open)} aria-expanded={open}>Phone alerts{enabled ? ' · On' : ''}</button>{open && <section aria-label="Phone alert settings"><strong>Website inquiries on your phone</strong><p>Get an alert even when Mill City is closed. Tap it to open the shared lead pool. Customer details stay off your lock screen.</p>{iphone && !installed ? <p>On iPhone or iPad: open this site in Safari → Share → Add to Home Screen. Open the Mill City icon, sign in, then return here to enable alerts. Requires iOS/iPadOS 16.4 or later.</p> : !supported ? <p>This browser does not support phone alerts. Try current Safari on iPhone or Chrome on Android.</p> : enabled ? <><button disabled={busy} onClick={test}>Send test notification</button><button className="secondary" disabled={busy} onClick={disable}>Turn off on this device</button></> : <button disabled={busy} onClick={enable}>Enable phone alerts</button>}<p role="status">{message}</p></section>}</div>;
}
