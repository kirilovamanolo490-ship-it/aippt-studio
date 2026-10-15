// Studio wrapper around the unmodified, separately locked Mediabunny runtime.
const fs = require('node:fs');
const path = require('node:path');
const runtime = path.resolve(__dirname, '../.tools/media-inspect-runtime/node_modules/mediabunny');
const packageInfo = JSON.parse(fs.readFileSync(path.join(runtime, 'package.json'), 'utf8'));
if (packageInfo.version !== '1.55.7') throw new Error('Mediabunny runtime version differs from reviewed 1.55.7');
const { Input, FilePathSource, MP4, MATROSKA, WAVE, MP3, OGG, FLAC, ADTS } = require(path.join(runtime, 'dist/bundles/mediabunny.node.cjs'));

(async () => {
  const input = new Input({source: new FilePathSource(process.argv[2]), formats: [MP4, MATROSKA, WAVE, MP3, OGG, FLAC, ADTS]});
  try {
    const tracks = [];
    for (const track of await input.getTracks()) {
      const row = {id: track.id, type: track.type, codec: await track.getCodec(),
        start_timestamp_seconds: await track.getFirstTimestamp(), end_timestamp_seconds: await track.computeDuration(),
        packet_stats: await track.computePacketStats()};
      if (track.isVideoTrack()) Object.assign(row, {width: await track.getDisplayWidth(), height: await track.getDisplayHeight()});
      if (track.isAudioTrack()) Object.assign(row, {sample_rate: await track.getSampleRate(), channels: await track.getNumberOfChannels()});
      tracks.push(row);
    }
    const report = {runtime_version: packageInfo.version, tracks, end_timestamp_seconds: await input.computeDuration(),
      decoded: false, audible_content_verified: false, semantic_sync_verified: false,
      verification: 'Container and encoded packet inspection; does not prove decoded sound or semantic synchronization.'};
    fs.writeFileSync(process.argv[3], JSON.stringify(report, null, 2));
  } finally { input.dispose(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
