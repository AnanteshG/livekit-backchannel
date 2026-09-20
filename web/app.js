const $ = (id) => document.getElementById(id);
const fmt = (n, unit = " ms") => (n == null ? "—" : `${n.toFixed(1)}${unit}`);
let room, readyTimer;
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
      body: JSON.stringify({ enabled: $("enabled").checked }),
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
$("enabled").onchange = async () => {
  if (room)
    try {
      await room.localParticipant.publishData(
        new TextEncoder().encode(
          JSON.stringify({ enabled: $("enabled").checked }),
        ),
        { reliable: true, topic: "lab.control" },
      );
      log(`Backchannels ${$("enabled").checked ? "enabled" : "disabled"}`);
    } catch (e) {
      log(`Mode change failed: ${e.message}`);
    }
};
$("clearEvents").onclick = () => $("liveEvents").replaceChildren();
fetch("/api/status")
  .then((r) => r.json())
  .then((status) => {
    $("availability").textContent = status.configured
      ? "Credentials configured. The agent worker must also be running."
      : `Live mode needs: ${status.missing.join(", ")}. See the README for setup.`;
  })
  .catch((e) => {
    $("availability").textContent =
      `Live mode status unavailable: ${e.message}`;
  });
