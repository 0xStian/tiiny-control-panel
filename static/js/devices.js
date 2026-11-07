/* Use the panel from a phone: a local-network listener that only paired devices can use. */
'use strict';

function qrSvg(text) {
  const q = qrcode(0, 'M');
  q.addData(text);
  q.make();
  const n = q.getModuleCount(), m = 4, size = n + m * 2;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('class', 'qr');
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'QR code with the pairing link');
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('width', size); bg.setAttribute('height', size); bg.setAttribute('fill', '#ffffff');
  let d = '';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c + m},${r + m}h1v1h-1z`;
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', d); path.setAttribute('fill', '#000000');
  svg.append(bg, path);
  return svg;
}

function devicesCard(ctx) {
  const body = h('div', {class: 'stack'}, loadingBlock());
  let pairTimer = null;
  ctx.onCleanup(() => clearInterval(pairTimer));
  async function load() {
    let st;
    try { st = await api('/api/lan'); } catch (e) { clear(body).append(errorBlock(e.message)); return; }
    if (ctx.alive()) render(st);
  }
  async function setEnabled(on) {
    try { render(await api('/api/lan', {method: 'POST', body: {enabled: on}})); }
    catch (e) { toast('Couldn’t change that', 'err', {detail: e.message}); load(); }
  }
  function render(st) {
    clearInterval(pairTimer);
    const port = st.port || location.port || '8765';
    const rows = [h('div', {class: 'row gap-sm'}, switchInput(st.enabled && st.running, setEnabled, 'Allow paired devices on your network'),
      h('span', {class: 'spacer'}, 'Allow paired phones and tablets on your network'))];
    if (st.error) rows.push(h('div', {class: 'banner warn'}, icon('alert'), h('div', {class: 'text small'}, st.error)));
    if (!st.running) {
      rows.push(h('p', {class: 'small muted'}, 'When this is on, the panel also listens on this computer’s local-network address. Only devices you pair with a QR code can use it; everything else gets a “not paired” page.'));
    } else {
      const pairBox = h('div');
      if (!st.tls) rows.push(h('div', {class: 'banner warn'}, icon('alert'), h('div', {class: 'text small'},
        h('strong', {}, 'Only on a network you trust'),
        'Phones connect without encryption (plain HTTP). Anyone who can see your Wi-Fi traffic could take over a paired phone’s connection, so use this on your home network, not on shared or public Wi-Fi. For an encrypted connection, start the panel with --tls-cert and --tls-key.')));
      rows.push(h('div', {class: 'stack-sm'},
        h('div', {class: 'small'}, 'Address on your network: ', h('code', {}, st.url)),
        h('div', {class: 'row wrap gap-sm'}, btn('Pair a phone', async () => {
          const p = await api('/api/lan/pair', {method: 'POST', body: {}});
          let left = p.expires_in;
          const count = h('span', {class: 'small muted'});
          const tick = () => { count.textContent = left > 0 ? `This code works once, for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} more.` : 'This code has expired.'; left--; };
          tick();
          clearInterval(pairTimer);
          pairTimer = setInterval(() => { tick(); if (left < -1) { clearInterval(pairTimer); clear(pairBox); } }, 1000);
          clear(pairBox).append(h('div', {class: 'pair-box'}, typeof qrcode === 'function' ? qrSvg(p.url) : null,
            h('div', {class: 'stack-sm'}, h('strong', {}, 'Scan this with the phone’s camera'),
              h('span', {class: 'small muted'}, 'The phone must be on the same network as this computer. Or open this link on it:'),
              h('code', {class: 'pair-url'}, p.url), count,
              h('div', {}, btn('Done', () => { clearInterval(pairTimer); clear(pairBox); load(); }, {small: true, kind: 'ghost'})))));
        }, {kind: 'primary', icon: 'phone'})), pairBox));
      rows.push(h('details', {class: 'small'}, h('summary', {}, 'The phone can’t connect?'),
        h('div', {class: 'stack-sm'}, h('p', {}, 'Check that both are on the same Wi-Fi. If this computer’s firewall blocks the port, allow it.'),
          h('p', {class: 'muted'}, 'Fedora, openSUSE and others with firewalld (add --permanent to keep it after a restart):'),
          h('code', {class: 'block'}, `sudo firewall-cmd --add-port=${port}/tcp`),
          h('p', {class: 'muted'}, 'Ubuntu, Debian, Mint and others with ufw:'),
          h('code', {class: 'block'}, `sudo ufw allow ${port}/tcp`),
          h('p', {class: 'muted'}, 'Arch has no firewall switched on unless you set one up.'),
          st.tls ? null : h('p', {class: 'muted'}, 'Browsers only allow the microphone on secure pages, so recording meetings from a phone needs HTTPS: start the panel with --tls-cert and --tls-key pointing at your own certificate.'))));
    }
    if (st.devices.length) {
      rows.push(h('div', {class: 'stack-sm'}, h('div', {class: 'section-title'}, 'Paired devices'), st.devices.map(d => h('div', {class: 'row'},
        icon('phone'), h('div', {class: 'spacer', style: {minWidth: 0}}, h('strong', {}, d.name), h('div', {class: 'cell-sub'}, `Paired ${fmtAgo(d.created)} · last seen ${fmtAgo(d.last_seen)}${d.ip ? ` · ${d.ip}` : ''}`)),
        btn('Remove', async () => {
          if (!(await confirmDialog({title: `Remove ${d.name}?`, message: 'It can’t use the panel any more until you pair it again.', confirmLabel: 'Remove', danger: true}))) return;
          await api(`/api/lan/devices/${d.id}`, {method: 'DELETE', body: {}});
          load();
        }, {small: true, kind: 'ghost', icon: 'trash'})))));
    }
    append(clear(body), rows);
  }
  load();
  return card({title: 'Phone and other devices', ic: 'phone', sub: 'Use the panel from your phone on the same network'}, body);
}
