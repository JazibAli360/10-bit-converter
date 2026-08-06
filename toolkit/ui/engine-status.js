/* Capability-aware UI hint for the optional libplacebo backend. */
(function () {
  function loadEngineStatus() {
    if (!window.fetch) return;
    var initialOption = document.querySelector('#engineSelect option[value="libplacebo-deband-v1"]');
    if (initialOption) initialOption.textContent = "High-quality GPU deband (checking…)";
    fetch("/api/engines", {headers: {"X-10bit-Token": window.API_TOKEN || ""}})
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        var experimental = (payload && payload.engines || []).find(function (engine) {
          return engine.id === "libplacebo-deband-v1";
        });
        var note = document.getElementById("engineCapabilityNote");
        if (!note || !experimental) return;
        var option = document.querySelector('#engineSelect option[value="libplacebo-deband-v1"]');
        if (option) {
          option.disabled = !experimental.available;
          option.textContent = experimental.available
            ? "High-quality GPU deband (experimental)"
            : "High-quality GPU deband (experimental · unavailable)";
        }
        note.textContent = experimental.available
          ? "Experimental GPU deband is ready. It preserves colour settings; it does not create new colour information."
          : "Experimental GPU deband is unavailable: " + (experimental.reason || "Vulkan could not initialize.") + " Faithful 10-bit remains ready.";
        note.hidden = false;
      })
      .catch(function () {
        if (initialOption) initialOption.textContent = "High-quality GPU deband (experimental · unavailable)";
        /* Capability hints must never block importing. */
      });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", loadEngineStatus);
  else loadEngineStatus();
}());
