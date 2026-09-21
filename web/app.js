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
  const width = canvas.width, height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.font = "11px Segoe UI";
  context.fillStyle = "#64767d";
  context.fillText("last 20 seconds", 10, 16);
  for (const [key, color, offset] of [["frustration", "#c65b50", 0], ["uncertainty", "#8774ba", 1], ["energy", "#147d73", 2]]) {
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.beginPath();
    expressionPoints.forEach((point, index) => {
      const x = 10 + (point.age / 20) * (width - 20);
      const y = height - 20 - point[key] * (height - 42);
      if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
    });
    context.stroke();
    context.fillStyle = color;
    context.fillText(key, 90 + offset * 120, 16);
  }
  context.fillStyle = "#1e3038";
  timelineMarkers.forEach((marker) => {
    const x = 10 + (marker.age / 20) * (width - 20);
    context.fillRect(x, height - 12, 2, 8);
    context.fillText(marker.label, Math.min(x + 3, width - 55), height - 4);
  });
}
function updateExpression(event) {
  for (const name of ["frustration", "uncertainty", "energy"]) {
    $(name).value = event[name];
    $(`${name}Value`).textContent = event[name].toFixed(2);
  }
  expressionPoints.push({ ...event, timestamp: performance.now() });
  const now = performance.now();
  expressionPoints = expressionPoints.filter((point) => now - point.timestamp <= 20000)
    .map((point) => ({ ...point, age: 1 - (now - point.timestamp) / 20000 }));
  timelineMarkers = timelineMarkers.filter((marker) => now - marker.timestamp <= 20000)
    .map((marker) => ({ ...marker, age: 1 - (now - marker.timestamp) / 20000 }));
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
