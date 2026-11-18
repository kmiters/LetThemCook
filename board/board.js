// Poll only visible views. An unchanged registry leaves reading position and focus intact.
const sync = document.getElementById("sync")
const revision = '"' + document.documentElement.dataset.revision + '"'
async function refresh() {
  if (document.hidden) return
  try {
    const response = await fetch(location.href, {
      method: "HEAD",
      cache: "no-store",
      headers: { "If-None-Match": revision },
    })
    if (response.status !== 304 && !response.ok) throw new Error()
    if (response.status !== 304 && response.headers.get("ETag") !== revision) {
      // Let the reader finish selecting or copying a brief.
      if (!window.getSelection()?.toString()) location.reload()
    } else {
      sync.textContent = "Read-only · Live"
    }
  } catch {
    sync.textContent = "Refresh unavailable · Retrying"
  }
}
refresh()
setInterval(refresh, 10000)
document.addEventListener("visibilitychange", refresh)
