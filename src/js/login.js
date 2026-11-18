const { Authflow, Titles } = require('prismarine-auth')
const { spawn } = require('child_process')
const path = require('path')

function openBrowser(url) {
  // Only open real https links
  if (!/^https:\/\//.test(url)) return
  try {
    spawn('cmd', ['/c', 'start', '""', url], { stdio: 'ignore', detached: true }).unref()
  } catch (e) { /* the link is still printed below */ }
}

function copyToClipboard(text) {
  try {
    const p = spawn('clip', [], { stdio: ['pipe', 'ignore', 'ignore'] })
    p.stdin.end(text)
  } catch (e) { /* ignore */ }
}

// Adds the code to the link so the page opens with the field pre-filled
function linkWithCode(uri, code) {
  try {
    const u = new URL(uri)
    u.searchParams.set('otc', code)
    return u.toString()
  } catch (e) {
    return uri
  }
}

// Sign-in instructions go to stderr, so stdout stays clean for the JSON
const onCode = (res) => {
  copyToClipboard(res.user_code)
  openBrowser(linkWithCode(res.verification_uri, res.user_code))
}

const flow = new Authflow(
  'mc-join',
  path.join(__dirname, '../../auth-cache'),
  { flow: 'sisu', authTitle: Titles.MinecraftJava, deviceType: 'Win32' },
  onCode
)

flow.getMinecraftJavaToken({ fetchProfile: true })
  .then((r) => {
    if (!r.profile || !r.profile.id) throw new Error('Mit diesem Microsoft-Konto ist kein Minecraft-Java-Profil verknüpft.')
    process.stdout.write(JSON.stringify({ name: r.profile.name, id: r.profile.id, token: r.token }))
  })
  .catch(() => {
    console.error('Die Anmeldung ist fehlgeschlagen. Prüfe dein Microsoft-Konto und versuche es erneut.')
    process.exit(1)
  })