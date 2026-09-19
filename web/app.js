const $ = id => document.getElementById(id);
const fmt = (n, unit = ' ms') => n == null ? '—' : `${n.toFixed(1)}${unit}`;
let report, specs = [], selected = 'long_monologue', repetition = '0', room, readyTimer;
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function tab(live) {
  $('experiment').hidden = live; $('live').hidden = !live;
  $('experimentTab').classList.toggle('active', !live);
  $('liveTab').classList.toggle('active', live);
}
$('experimentTab').onclick = () => tab(false);
$('liveTab').onclick = () => tab(true);
async function load() {
  const kind = $('source').value;
  $('download').href = `/api/results/${kind}`;
  try {
    const response = await fetch(`/api/results/${kind}`);
    if (!response.ok) throw new Error('No LiveKit measurements yet. Configure credentials, start the worker, then run the audio replay benchmark.');
    report = await response.json();
    $('notice').textContent = kind === 'simulation'
      ? 'SIMULATED DATA · Real engine, scripted speech signals and virtual provider delays. These numbers test policy behaviour; they do not demonstrate real LiveKit latency or a performance improvement.'
      : 'LIVE AUDIO REPLAY · Response latency uses PCM received by the replay client. Backchannel latency includes clock-alignment uncertainty. Hardware speaker latency is not measured.';
    renderSummary(); renderScenarios(); renderPair();
  } catch (error) {
    report = null;
    $('notice').textContent = error.message;
    for (const id of ['stats','metrics','timelines','events','scenarioList']) $(id).replaceChildren();
    $('pairCount').textContent = 'No measurements'; $('confidence').textContent = '';
    $('scenarioTitle').textContent = 'Awaiting audio replay'; $('duration').textContent = '';
    $('audio').removeAttribute('src'); $('repetition').replaceChildren();
  }
}
function renderSummary() {
  const s = report.summary, a = s.modes.baseline, b = s.modes.enabled;
  const cards = [
    ['Response P50', fmt(b.response_p50_ms), `Baseline ${fmt(a.response_p50_ms)}`],
    ['Response P95', fmt(b.response_p95_ms), `${b.valid} valid enabled runs`],
    ['Backchannel P50', fmt(b.bc_p50_ms), 'Decision → received audio'],
    ['Near-end collisions', String(b.eot_collisions), `${b.cancelled} cancelled acknowledgements`]
  ];
  $('stats').replaceChildren(...cards.map(([label,value,note]) => {
    const card = element('div', null, 'stat');
    card.append(element('div', label, 'stat-label'), element('div', value, 'stat-value'), element('div', note, 'stat-note'));
    return card;
  }));
  const metrics = [
    ['Response P50', 'response_p50_ms', true], ['Response P95', 'response_p95_ms', true],
    ['LLM first token · P50', 'llm_ttft_ms', true], ['TTS first audio · P50', 'tts_first_ms', true],
    ['End-of-turn detection · P50', 'eot_ms', true], ['STT finalization · P50', 'stt_final_ms', true],
    ['Backchannels', 'backchannels', false], ['Near-end collisions', 'eot_collisions', false],
    ['Cancelled backchannels', 'cancelled', false], ['Overlap with user · total', 'user_overlap_ms', true],
    ['Overlap with response · total', 'response_overlap_ms', true], ['Premature responses', 'premature_response', false],
    ['Failed / incomplete runs', 'failures', false]
  ];
  $('metrics').replaceChildren(...metrics.map(([label,key,ms]) => {
    const row = element('tr');
    const delta = a[key] == null || b[key] == null ? null : b[key] - a[key];
    for (const text of [label, ms ? fmt(a[key]) : String(a[key]), ms ? fmt(b[key]) : String(b[key]),
      delta == null ? '—' : `${delta > 0 ? '+' : ''}${ms ? fmt(delta) : delta}`]) row.append(element('td', text));
    return row;
  }));
  $('pairCount').textContent = `${s.complete_pairs} complete pairs`;
  const ci = s.paired_mean_ci95_ms;
  $('confidence').textContent = `Paired mean difference ${fmt(s.paired_mean_delta_ms)} · Stratified bootstrap 95% interval [${fmt(ci[0])}, ${fmt(ci[1])}]. ${report.measurement_kind === 'simulation' ? 'Zero delta is built into the virtual provider schedule, not evidence of real-world equivalence.' : s.interpretation} ${s.regression_flag ? 'Regression flag: lower confidence bound exceeds 30 ms.' : ''}`;
}
function renderScenarios() {
  $('scenarioList').replaceChildren(...specs.map((spec, i) => {
    const button = element('button', null, `scenario ${selected === spec.id ? 'selected' : ''}`);
    button.append(element('span', String(i+1).padStart(2,'0'), 'num'), element('span', spec.label));
    button.onclick = () => { selected = spec.id; renderScenarios(); renderPair(); };
    return button;
  }));
  const values = [...new Set(report.runs.filter(r => r.scenario === selected && !r.warmup).map(r => r.pair_id.split('-').at(-1)))];
  if (!values.includes(repetition)) repetition = values[0];
  $('repetition').replaceChildren(...values.map(v => {
    const o = element('option', `Pair ${Number(v) + 1}`); o.value = v; return o;
  }));
  $('repetition').value = repetition;
}
function renderPair() {
  if (!report) return;
  const spec = specs.find(s => s.id === selected);
  const pair = report.runs.filter(r => r.pair_id === `${selected}-${repetition}` && !r.warmup)
    .sort((a,b) => a.mode.localeCompare(b.mode));
  $('scenarioTitle').textContent = spec.label;
  $('duration').textContent = `${spec.speech_end.toFixed(1)}s user turn`;
  if (!$('audio').src.endsWith(spec.file.split('/').at(-1))) $('audio').src = `/audio/${spec.file.split('/').at(-1)}`;
  const max = Math.max(spec.duration, ...pair.flatMap(r => r.events.map(e => e.t)), 1) + .4;
  $('timelines').replaceChildren(...pair.map(run => timeline(run, spec, max)));
  $('events').replaceChildren(...pair.flatMap(run => run.events.map(event => {
    const row = element('tr');
    const { t, kind, ...data } = event;
    for (const text of [run.mode, `${t.toFixed(3)}s`, kind, JSON.stringify(data)]) row.append(element('td', text));
    return row;
  })));
}
function timeline(run, spec, max) {
  const wrap = element('div', null, 'timeline-wrap');
  const title = element('div', null, 'timeline-title');
  title.append(element('span', run.mode === 'baseline' ? 'A / Baseline' : 'B / Backchannel enabled'),
    element('span', run.status === 'ok' ? `Response ${fmt(run.metrics.response_ms)} · ${run.metrics.backchannels} acknowledgements` : `FAILED: ${run.error || 'incomplete measurement'}`, 'timeline-sub'));
  wrap.append(title);
  const pos = time => `${Math.max(0,Math.min(100,time / max * 100))}%`;
  for (const name of ['User','STT','EOT risk','Backchannel','Response']) {
    const row = element('div', null, 'track-row'); row.append(element('span', name, 'lane-label'));
    const lane = element('div', null, 'lane'); row.append(lane);
    const end = element('span', null, 'endline'); end.style.left = pos(spec.speech_end); lane.append(end);
    const bar = (start, finish, cls, hint) => {
      const b = element('span', null, `bar ${cls}`); b.style.left = pos(start);
      b.style.width = `${Math.max(.35, (finish-start)/max*100)}%`; b.title = hint; lane.append(b);
    };
    const marker = (event, symbol) => {
      const m = element('span', symbol, 'marker'); m.style.left = pos(event.t);
      m.title = `${event.t.toFixed(3)}s ${event.kind} ${event.text || (event.value ?? '')}`; lane.append(m);
    };
    if (name === 'User') for (const s of spec.segments) bar(s.start,s.end,'',s.text);
    if (name === 'STT') for (const e of run.events.filter(e=>e.kind.startsWith('stt_'))) marker(e,e.kind==='stt_final'?'●':'·');
    if (name === 'EOT risk') for (const e of run.events.filter(e=>e.kind==='eot_risk')) marker(e,e.value.toFixed(1));
    if (name === 'Backchannel') {
      for (const e of run.events.filter(e=>e.kind==='bc_decision')) marker(e,'▲');
      for (const e of run.events.filter(e=>e.kind==='bc_audio_received')) {
        const finish=run.events.find(x=>x.kind==='bc_audio_received_end'&&x.decision===e.decision&&x.t>=e.t);
        bar(e.t, finish ? finish.t : e.t+.06,'bc',`Received acknowledgement at ${e.t.toFixed(3)}s`);
      }
    }
    if (name === 'Response') for (const e of run.events.filter(e=>e.kind==='response_audio_received')) marker(e,'●');
    wrap.append(row);
  }
  const ticks = element('div', null, 'ticks');
  for(let i=0;i<=5;i++) ticks.append(element('span', `${(max*i/5).toFixed(1)}s`));
  wrap.append(ticks); return wrap;
}
$('source').onchange = load;
$('repetition').onchange = () => { repetition = $('repetition').value; renderPair(); };
function log(text) {
  const item = element('li'); item.append(element('time', new Date().toLocaleTimeString()), element('span', text));
  $('liveEvents').prepend(item);
  while ($('liveEvents').children.length > 150) $('liveEvents').lastChild.remove();
}
function resetLive(message) {
  clearTimeout(readyTimer); $('liveState').textContent = message;
  $('connect').disabled = false; $('disconnect').disabled = true;
  $('orb').classList.remove('on'); $('remoteAudio').replaceChildren();
}
$('connect').onclick = async () => {
  $('connect').disabled = true; $('liveState').textContent = 'Connecting…';
  try {
    if (!window.LivekitClient) throw new Error('LiveKit browser library failed to load.');
    const response = await fetch('/api/connect', {method:'POST', headers:{'Content-Type':'application/json','X-Demo-Key':$('demoKey').value}, body:JSON.stringify({enabled:$('enabled').checked})});
    const config = await response.json(); if (!response.ok) throw new Error(config.detail || 'Connection failed');
    const LK = window.LivekitClient;
    room = new LK.Room({adaptiveStream:true,dynacast:true});
    room.on(LK.RoomEvent.TrackSubscribed,(track,publication) => {
      if(track.kind==='audio') {
        const audio=track.attach(); audio.controls=false; $('remoteAudio').append(audio);
        audio.addEventListener('playing',()=>log(`Browser audio playing: ${publication.trackName}`));
      }
    });
    room.on(LK.RoomEvent.TrackUnsubscribed,track=>track.detach().forEach(el=>el.remove()));
    room.on(LK.RoomEvent.Disconnected,()=>{room=null;resetLive('Conversation ended');});
    room.on(LK.RoomEvent.DataReceived,(payload,participant,kind,topic)=> {
      if(topic!=='lab.events') return;
      try {
        const {event}=JSON.parse(new TextDecoder().decode(payload));
        if(event.kind==='clock_pong') return;
        log(`${event.t.toFixed(2)}s · ${event.kind}${event.text?' · '+event.text:''}${event.state?' · '+event.state:''}`);
        if(event.kind==='ready') {clearTimeout(readyTimer);$('liveState').textContent='The agent is listening';$('orb').classList.add('on');}
        if(event.kind==='agent_state') $('liveState').textContent=`Agent ${event.state}`;
        if(event.kind==='provider_error') $('liveHelp').textContent='The agent reported a provider error. Check the worker logs.';
      } catch(error) { log('Could not decode agent event'); }
    });
    await room.connect(config.url,config.token);
    await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);
    $('disconnect').disabled=false; $('liveHelp').textContent='Microphone connected. Wait for the agent to be ready, then speak.';
    log('Microphone connected; waiting for worker');
    readyTimer=setTimeout(()=>{ if(room) {$('liveHelp').textContent='No worker ready signal within 60 seconds. Check worker startup and credentials.';log('Worker readiness timed out');}},60000);
  } catch(error) {
    if(room) {await room.disconnect();room=null;}
    resetLive('Could not connect'); $('liveHelp').textContent=error.message; log(error.message);
  }
};
$('disconnect').onclick=async()=>{if(room)await room.disconnect();room=null;resetLive('Conversation ended');};
$('enabled').onchange=async()=>{if(room)try{await room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify({enabled:$('enabled').checked})),{reliable:true,topic:'lab.control'});log(`Backchannels ${$('enabled').checked?'enabled':'disabled'}`);}catch(e){log(`Mode change failed: ${e.message}`);}};
$('clearEvents').onclick=()=>$('liveEvents').replaceChildren();
Promise.all([fetch('/api/scenarios').then(r=>r.json()),fetch('/api/status').then(r=>r.json())]).then(([s,status])=>{
  specs=s;
  $('availability').textContent=status.configured?'Credentials configured. The agent worker must also be running.':`Live mode needs: ${status.missing.join(', ')}. See the README for setup.`;
  load();
}).catch(e=>{$('notice').textContent=`Could not load the experiment: ${e.message}`;});
