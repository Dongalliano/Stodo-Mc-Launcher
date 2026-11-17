const { Authflow, Titles } = require('prismarine-auth')
const path = require('path')

// Sign-in instructions go to stderr, so stdout stays clean for the JSON
const onCode = (res) => console.error(`\nOpen ${res.verification_uri} and enter the code: ${res.user_code}\n`)

const flow = new Authflow(
  'mc-join',
  path.join(__dirname, 'auth-cache'),
  { flow: 'sisu', authTitle: Titles.MinecraftJava, deviceType: 'Win32' },
  onCode
)

flow.getMinecraftJavaToken({ fetchProfile: true })
  .then((r) => {
    if (!r.profile || !r.profile.id) throw new Error('No Minecraft profile (does the account own Java Edition?)')
    process.stdout.write(JSON.stringify({ name: r.profile.name, id: r.profile.id, token: r.token }))
  })
  .catch((e) => { console.error('Login failed:', e.message || e); process.exit(1) })