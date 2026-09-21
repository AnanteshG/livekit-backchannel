const $ = (id) => document.getElementById(id);
const fmt = (n, unit = " ms") => (n == null ? "—" : `${n.toFixed(1)}${unit}`);
let room, readyTimer;
let expressionPoints = [];
let timelineMarkers = [];
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function showSection(selectedSection) {
  for (const name of ["approach", "live"]) {
    $(name).hidden = name !== selectedSection;
    $(`${name}Tab`).classList.toggle("active", name === selectedSection);
    $(`${name}Tab`).setAttribute(
      "aria-pressed",
      String(name === selectedSection),
    );
  }
}
for (const name of ["approach", "live"]) {
  $(`${name}Tab`).onclick = () => showSection(name);
}
document.querySelectorAll("[data-view]").forEach((link) => {
  link.onclick = (event) => {
    event.preventDefault();
    showSection(link.dataset.view);
    document.querySelector("nav").scrollIntoView({ block: "start" });
  };
});
showSection("approach");
function log(text) {
  const item = element("li");
  item.append(
    element("time", new Date().toLocaleTimeString()),
    element("span", text),
  );
  $("liveEvents").prepend(item);
  while ($("liveEvents").children.length > 150)
    $("liveEvents").lastChild.remove();
}
function resetLive(message) {
  clearTimeout(readyTimer);
  $("liveState").textContent = message;
  $("connect").disabled = false;
  $("disconnect").disabled = true;
  $("orb").classList.remove("on");
  $("remoteAudio").replaceChildren();
}
function drawExpressionTimeline() {
  const canvas = $("expressionTimeline");
  const context = canvas.getContext("2d");
  const width = canvas.clientWidth;
  if (!width) return;
  const height = 390, ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const now = performance.now();
  expressionPoints = expressionPoints.filter(point => now - point.timestamp <= 20000);
  timelineMarkers = timelineMarkers.filter(marker => now - marker.timestamp <= 20000);
  const left = 34, right = width - 14;
  const xFor = timestamp => left + Math.max(0, Math.min(1,
    1 - (now - timestamp) / 20000)) * (right - left);
  const cues = [["frustration", "Frustration cue", "#b9473d"],
    ["uncertainty", "Uncertainty cue", "#7552aa"], ["energy", "Energy", "#147d73"]];
  cues.forEach(([key, label, color], row) => {
    const top = row * 104 + 30, bottom = top + 64;
    context.font = "600 13px Segoe UI";
    context.fillStyle = color;
    context.fillText(label, left, top - 12);
    context.font = "11px Segoe UI";
    for (const value of [0, .5, 1]) {
      const y = bottom - value * 64;
      context.strokeStyle = "#e0e7ec";
      context.lineWidth = 1;
      context.beginPath(); context.moveTo(left, y); context.lineTo(right, y); context.stroke();
      context.fillStyle = "#63717e";
      context.fillText(String(value), 6, y + 4);
    }
    context.strokeStyle = color;
    context.lineWidth = 2.5;
    context.lineJoin = "round";
    context.beginPath();
    expressionPoints.forEach((point, index) => {
      const x = xFor(point.timestamp);
      const y = bottom - Math.max(0, Math.min(1, point[key])) * 64;
      if (index === 0 || point.timestamp - expressionPoints[index - 1].timestamp > 1500)
        context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    const last = expressionPoints.at(-1);
    if (last) {
      context.fillStyle = color;
      context.beginPath(); context.arc(xFor(last.timestamp), bottom - Math.max(0, Math.min(1, last[key])) * 64, 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillStyle = "#63717e";
      context.font = "12px Segoe UI";
      context.fillText("Speak to see this cue", left + 12, top + 36);
    }
  });
  context.font = "11px Segoe UI";
  context.fillStyle = "#63717e";
  for (const [fraction, label] of [[0, "20s ago"], [.5, "10s ago"], [1, "Now"]]) {
    context.textAlign = fraction === 0 ? "left" : fraction === 1 ? "right" : "center";
    context.fillText(label, left + fraction * (right - left), 327);
  }
  context.textAlign = "left";
  const markerRows = { ack: 349, EOT: 365, answer: 381 };
  timelineMarkers.forEach((marker) => {
    const y = markerRows[marker.label] || 349;
    context.fillStyle = "#314852";
    const progress = Math.max(0, Math.min(1, 1 - (now - marker.timestamp) / 20000));
    context.fillRect(152 + progress * (right - 152), y - 7, 3, 9);
  });
  context.fillStyle = "#63717e";
  context.fillText("Acknowledgement", left, 349);
  context.fillText("End of turn", left, 365);
  context.fillText("Answer", left, 381);
}
new ResizeObserver(drawExpressionTimeline).observe($("expressionTimeline"));
setInterval(() => { if (!$("live").hidden) drawExpressionTimeline(); }, 250);
function updateExpression(event) {
  for (const name of ["frustration", "uncertainty", "energy"]) {
    $(name).value = event[name];
    $(`${name}Value`).textContent = event[name].toFixed(2);
  }
  expressionPoints.push({ ...event, timestamp: performance.now() });
  $("acousticMeta").textContent = `${event.audio_seconds.toFixed(2)}s window · ${event.inference_ms.toFixed(1)}ms inference · confidence ${event.confidence.toFixed(2)}`;
  drawExpressionTimeline();
}
$("connect").onclick = async () => {
  let workerReady = false;
  $("connect").disabled = true;
  $("liveState").textContent = "Connecting…";
  try {
    if (!window.LivekitClient)
      throw new Error("LiveKit browser library failed to load.");
    const response = await fetch("/api/connect", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Demo-Key": $("demoKey").value,
      },
      body: JSON.stringify({ enabled: $("enabled").checked, acoustic_enabled: $("acousticEnabled").checked }),
    });
    const config = await response.json();
    if (!response.ok) throw new Error(config.detail || "Connection failed");
    const LK = window.LivekitClient;
    room = new LK.Room({ adaptiveStream: true, dynacast: true });
    room.on(LK.RoomEvent.TrackSubscribed, (track, publication) => {
      if (track.kind === "audio") {
        const audio = track.attach();
        audio.controls = false;
        $("remoteAudio").append(audio);
        audio.addEventListener("playing", () =>
          log(`Browser audio playing: ${publication.trackName}`),
        );
      }
    });
    room.on(LK.RoomEvent.TrackUnsubscribed, (track) =>
      track.detach().forEach((el) => el.remove()),
    );
    room.on(LK.RoomEvent.Disconnected, () => {
      room = null;
      resetLive("Conversation ended");
    });
    room.on(LK.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
      if (topic !== "lab.events") return;
      try {
        const { event } = JSON.parse(new TextDecoder().decode(payload));
        if (event.kind === "clock_pong") return;
        log(
          `${event.t.toFixed(2)}s · ${event.kind}${event.text ? " · " + event.text : ""}${event.state ? " · " + event.state : ""}`,
        );
        if (event.kind === "ready") {
          workerReady = true;
          clearTimeout(readyTimer);
          $("liveState").textContent = "The agent is listening";
          $("orb").classList.add("on");
        }
        if (event.kind === "acoustic_prediction") updateExpression(event);
        const markerNames = { bc_audio_submitted: "ack", eot_detected: "EOT", tts_first_audio: "answer" };
        if (markerNames[event.kind]) {
          timelineMarkers.push({ label: markerNames[event.kind], timestamp: performance.now(), age: 1 });
          drawExpressionTimeline();
        }
        if (event.kind === "agent_state")
          $("liveState").textContent = `Agent ${event.state}`;
        if (event.kind === "provider_error")
          $("liveHelp").textContent =
            "The agent reported a provider error. Check the worker logs.";
      } catch (error) {
        log("Could not decode agent event");
      }
    });
    await room.connect(config.url, config.token);
    await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);
    $("disconnect").disabled = false;
    $("liveHelp").textContent =
      "Microphone connected. Wait for the agent to be ready, then speak.";
    log("Microphone connected; waiting for worker");
    if (!workerReady)
      readyTimer = setTimeout(async () => {
        if (room && !workerReady) {
          await room.disconnect();
          resetLive("Agent offline");
          $("liveHelp").textContent =
            "The website is online, but the voice worker is unavailable. Start the worker, then try again.";
          log("Worker unavailable. Microphone disconnected; you can retry.");
        }
      }, 60000);
  } catch (error) {
    if (room) {
      await room.disconnect();
      room = null;
    }
    resetLive("Could not connect");
    $("liveHelp").textContent = error.message;
    log(error.message);
  }
};
$("disconnect").onclick = async () => {
  if (room) await room.disconnect();
  room = null;
  resetLive("Conversation ended");
};
async function sendModes() {
  if (room)
    try {
      await room.localParticipant.publishData(
        new TextEncoder().encode(
          JSON.stringify({ enabled: $("enabled").checked, acoustic_enabled: $("acousticEnabled").checked }),
        ),
        { reliable: true, topic: "lab.control" },
      );
      log(`Backchannels ${$("enabled").checked ? "on" : "off"}; acoustics ${$("acousticEnabled").checked ? "on" : "off"}`);
    } catch (e) {
      log(`Mode change failed: ${e.message}`);
    }
}
$("enabled").onchange = sendModes;
$("acousticEnabled").onchange = sendModes;
$("clearEvents").onclick = () => $("liveEvents").replaceChildren();
fetch("/api/status")
  .then((r) => r.json())
  .then((status) => {
    $("availability").textContent = status.configured
      ? "Live mode is configured. Connect to test the cloud worker."
      : `Live mode needs: ${status.missing.join(", ")}. See the README for setup.`;
  })
  .catch((e) => {
    $("availability").textContent =
      `Live mode status unavailable: ${e.message}`;
  });
