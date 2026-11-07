/* Integrations: use your Tiiny from other apps and code, with settings filled in and a test for each API. */
'use strict';

function viewIntegrations(root, ctx) {
  const info = S.health?.device || {};
  const host = info.address ? `${info.address.includes(':') ? `[${info.address}]` : info.address}${info.port && info.port !== 80 ? `:${info.port}` : ''}` : '<device-ip>';
  const BASE = `http://${host}`;
  let key = null;  // the real key, only after Reveal
  let model = '';
  const keyShown = () => key || 'YOUR_TIINY_API_KEY';
  const out = h('div', {class: 'stack'});
  const keyLine = h('code', {}, info.key_hint || '(no key: log in on the Account page)');
  const modelSel = h('select', {class: 'input sm', style: {width: 'auto', maxWidth: 'min(300px, 100%)'}, 'aria-label': 'Model for the examples'});
  const fillModels = () => {
    const list = modelsOf('chat').sort((a, b) => S.running.has(b.id) - S.running.has(a.id));
    clear(modelSel);
    for (const m of list) modelSel.append(h('option', {value: m.id}, `${S.running.has(m.id) ? '● ' : ''}${m.display_name || m.name}`));
    if (!list.length) modelSel.append(h('option', {value: 'default'}, 'default'));
    if (list.some(m => m.id === model)) modelSel.value = model; else model = modelSel.value;
  };
  modelSel.addEventListener('change', () => { model = modelSel.value; render(); });
  const tests = h('div', {class: 'stack-sm'});
  const testBtn = (label, fn) => btn(label, async () => {
    const row = h('div', {class: 'row small'}, h('span', {class: 'spinner'}), `${label}…`);
    tests.prepend(row);
    const t0 = performance.now();
    let ok = false, detail = '';
    try { [ok, detail] = await fn(); } catch (e) { detail = e.message; }
    const ms = Math.round(performance.now() - t0);
    clear(row).append(badge(ok ? 'Works' : 'Failed', ok ? 'ok' : 'err', ok ? 'check' : 'alert'), h('strong', {}, label.replace(/^Test the /, '')), h('span', {class: 'muted'}, `${ms} ms · ${detail}`));
  }, {small: true, icon: 'play'});
  const needModel = () => S.running.has(model) || [...S.running].some(id => S.modelsById.get(id) && TYPES.chat(S.modelsById.get(id)));
  root.append(
    h('div', {class: 'banner info'}, icon('plug'), h('div', {class: 'text'}, h('strong', {}, 'Your Tiiny works with apps made for OpenAI, Anthropic (Claude) and Ollama'),
      h('span', {class: 'small'}, 'Point an app at the addresses below and it runs on your device. It only works for apps on your own network: cloud services can’t reach your Tiiny.'))),
    card({title: 'Connection details', ic: 'link'}, h('div', {class: 'stack'},
      kvList([['OpenAI-compatible', h('code', {}, `${BASE}/v1`)], ['Anthropic-compatible', h('code', {}, BASE)], ['Ollama-compatible', h('code', {}, BASE)],
        ['API key', h('span', {class: 'row gap-sm'}, keyLine,
          btn('Reveal', async () => { key = (await api('/api/device/key')).key; keyLine.textContent = key || '(none)'; render(); }, {small: true, kind: 'ghost', icon: 'eye'}),
          btn('Copy', async () => { const k = key || (await api('/api/device/key')).key; copyText(k, 'API key copied'); }, {small: true, kind: 'ghost', icon: 'copy'}))],
        ['Model in the examples', modelSel]]),
      h('p', {class: 'small muted'}, 'The examples below fill in the key only after you press Reveal, so it doesn’t end up in a screenshot by accident. A model has to be loaded before an app can use it; “default” means whichever chat model is loaded.'),
      h('div', {class: 'row wrap gap-sm'},
        testBtn('Test the OpenAI API', async () => {
          const r = await dev('/v1/models', {quiet: true, silent: true});
          return [r.ok, r.ok ? `${(r.json?.data || []).length} model(s) loaded` : r.error];
        }),
        testBtn('Test the Anthropic API', async () => {
          if (!needModel()) return [false, 'load a chat model first'];
          const r = await dev('/v1/messages', {method: 'POST', json: {model: S.running.has(model) ? model : 'default', max_tokens: 60, thinking: {type: 'disabled'}, messages: [{role: 'user', content: 'Reply with just: hello'}]}, quiet: true, silent: true, timeout: 120});
          const text = (r.json?.content || []).map(c => c.text || '').join('').trim();
          return [r.ok, r.ok ? (text ? `replied “${text.slice(0, 40)}”` : 'answered (only thinking so far)') : r.error];
        }),
        testBtn('Test the Ollama API', async () => {
          const r = await dev('/api/tags', {quiet: true, silent: true});
          return [r.ok, r.ok ? `${(r.json?.models || []).length} model(s) listed` : r.error];
        }),
        testBtn('Test decision models', async () => {
          const loaded = modelsOf('decide').find(m => S.running.has(m.id));
          if (!loaded) return [false, modelsOf('decide').length ? 'load a decision model first' : 'no decision model on your Tiiny'];
          const r = await dev('/v1/systemone', {method: 'POST', json: {model: loaded.id, state: 'The sky is blue.', questions: {t: {type: 'noul', instructions: 'Is this statement true?'}}}, quiet: true, silent: true});
          const p = r.json?.answers?.t?.noul;
          return [r.ok, r.ok ? `${shortName(loaded.id)}: “the sky is blue” is true with ${Math.round((p ?? 0) * 100)}%` : r.error];
        })),
      tests)),
    out);

  function snippet(title, lang, code, note) {
    return h('div', {class: 'snippet'}, h('div', {class: 'snippet-head'}, h('strong', {}, title), h('span', {class: 'faint small'}, lang),
      h('span', {class: 'spacer'}), iconBtn('copy', `Copy ${title}`, () => copyText(code, `${title} copied`))),
      note ? h('p', {class: 'small muted'}, note) : null, h('pre', {class: 'code-block'}, code));
  }
  function render() {
    const K = keyShown(), M = model || 'default';
    const groups = [
      ['Code', [
        snippet('OpenAI SDK', 'Python', `from openai import OpenAI\n\nclient = OpenAI(base_url="${BASE}/v1", api_key="${K}")\nreply = client.chat.completions.create(\n    model="${M}",\n    messages=[{"role": "user", "content": "Hello!"}],\n)\nprint(reply.choices[0].message.content)`),
        snippet('OpenAI SDK', 'JavaScript / TypeScript', `import OpenAI from "openai";\n\nconst client = new OpenAI({ baseURL: "${BASE}/v1", apiKey: "${K}" });\nconst reply = await client.chat.completions.create({\n  model: "${M}",\n  messages: [{ role: "user", content: "Hello!" }],\n});\nconsole.log(reply.choices[0].message.content);`),
        snippet('curl', 'shell', `curl ${BASE}/v1/chat/completions \\\n  -H "Authorization: Bearer ${K}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${M}", "messages": [{"role": "user", "content": "Hello!"}]}'`),
        snippet('Anthropic SDK', 'Python', `import anthropic\n\nclient = anthropic.Anthropic(base_url="${BASE}", api_key="${K}")\nreply = client.messages.create(\n    model="${M}",\n    max_tokens=1024,\n    messages=[{"role": "user", "content": "Hello!"}],\n)\nprint(reply.content[0].text)`),
        snippet('LangChain', 'Python', `from langchain_openai import ChatOpenAI\n\nllm = ChatOpenAI(base_url="${BASE}/v1", api_key="${K}", model="${M}")\nprint(llm.invoke("Hello!").content)`),
        snippet('LlamaIndex', 'Python', `from llama_index.llms.openai_like import OpenAILike\n\nllm = OpenAILike(api_base="${BASE}/v1", api_key="${K}", model="${M}", is_chat_model=True)\nprint(llm.complete("Hello!"))`),
      ]],
      ['Chat apps', [
        snippet('Open WebUI', 'Docker', `docker run -d -p 3000:8080 \\\n  -e OPENAI_API_BASE_URL=${BASE}/v1 \\\n  -e OPENAI_API_KEY=${K} \\\n  -v open-webui:/app/backend/data --name open-webui \\\n  ghcr.io/open-webui/open-webui:main`, 'Or in a running Open WebUI: Settings → Connections → OpenAI API, with the address and key above.'),
        snippet('Obsidian Copilot', 'settings', `Settings → Copilot → Model → Add custom model\n  Model name: ${M}\n  Provider:   OpenAI Format\n  Base URL:   ${BASE}/v1\n  API key:    ${K}`),
        snippet('Apps made for Ollama', 'settings', `Ollama server address:  ${BASE}\n(no API key needed for most Ollama apps; if the app asks, use ${K})`, 'Enchanted, Msty, Page Assist and other Ollama apps can use your Tiiny directly.'),
      ]],
      ['Editors and coding agents', [
        snippet('Continue', 'config.yaml', `models:\n  - name: Tiiny ${shortName(M)}\n    provider: openai\n    model: ${M}\n    apiBase: ${BASE}/v1\n    apiKey: ${K}\n    roles: [chat, edit, apply]`, 'In ~/.continue/config.yaml.'),
        snippet('Zed', 'settings.json', `{\n  "language_models": {\n    "openai_compatible": {\n      "Tiiny": {\n        "api_url": "${BASE}/v1",\n        "available_models": [{ "name": "${M}", "max_tokens": 32768 }]\n      }\n    }\n  }\n}`, 'Zed asks for the API key the first time you pick the provider.'),
        snippet('aider', 'shell', `export OPENAI_API_BASE=${BASE}/v1\nexport OPENAI_API_KEY=${K}\naider --model openai/${M}`),
        snippet('llm (Simon Willison’s CLI)', 'extra-openai-models.yaml', `- model_id: tiiny\n  model_name: ${M}\n  api_base: "${BASE}/v1"\n  api_key_name: tiiny`, `Put this in the file shown by: dirname "$(llm logs path)"; then run: llm keys set tiiny, and llm -m tiiny "Hello!"`),
      ]],
      ['Decision models (System One)', decisionSnippets(K)],
    ];
    append(clear(out), groups.map(([title, items]) => card({title, ic: title === 'Code' ? 'terminal' : title === 'Chat apps' ? 'chat' : 'edit'}, h('div', {class: 'snippets'}, items))));
  }
  /* Decision models (KnowLine, d3): typed questions in, a probability for every answer out. */
  const decisionModel = () => (modelsOf('decide').find(m => S.running.has(m.id)) || modelsOf('decide')[0])?.id || 'PelaAI/KnowLine-4B-Gen5.5';
  function decisionSnippets(K) {
    const D = decisionModel();
    const body = `{
    "model": "${D}",
    "state": "Customer message: I was charged twice and want my money back.",
    "questions": {
` +
      `      "team": {"type": "choice", "instructions": "Which team should handle this?", "criteria": {"billing": "payments and refunds", "shipping": "delivery and tracking", "other": "anything else"}},
` +
      `      "angry": {"type": "noul", "instructions": "Is the customer upset?"},
` +
      `      "urgency": {"type": "score", "instructions": "How urgent is it?", "criteria": ["low", "medium", "high"]}
    }
  }`;
    return [
      snippet('curl', 'shell', `curl ${BASE}/v1/systemone \
  -H "Authorization: Bearer ${K}" \
  -H "Content-Type: application/json" \
  -d '${body}'`,
        'Decision models answer with a probability for every option instead of writing text: "noul" is yes/no, "choice" picks one, "score" rates on a scale (lowest first). Up to 64 questions per request; add "images": ["data:image/png;base64,…"] to ask about a picture.'),
      snippet('Python', 'requests', `import requests

r = requests.post(
    "${BASE}/v1/systemone",
    headers={"Authorization": "Bearer ${K}"},
    json=${body},
)
for name, answer in r.json()["answers"].items():
    print(name, answer)`,
        'Each answer has the probabilities, plus "choice" (the most likely option), "noul" (the chance of yes) or "score" (the expected level, 0 = lowest).'),
    ];
  }
  const ready = () => { fillModels(); render(); };
  if (!S.modelsAt) refreshModels().then(() => { if (ctx.alive()) ready(); }); else ready();
  ctx.on('running', () => { fillModels(); });
}

registerView({id: 'integrations', order: 82, group: 'device', title: 'Integrations', ic: 'link', sub: 'Use your Tiiny from other apps and your own code', render: viewIntegrations});
