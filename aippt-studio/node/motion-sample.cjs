const fs = require('node:fs');
const [engine, request] = process.argv.slice(2);
const {gsap} = require(engine);
const {ease='power2.inOut', duration=1, samples=11} = JSON.parse(fs.readFileSync(request, 'utf8'));
if (!Number.isFinite(duration) || duration <= 0 || duration > 3600 || !Number.isInteger(samples) || samples < 2 || samples > 1000) throw new Error('invalid duration or sample count');
if (!/^(none|linear|power[1-4]\.(in|out|inOut)|sine\.(in|out|inOut)|expo\.(in|out|inOut))$/.test(ease)) throw new Error('unsupported easing');
const fn = gsap.parseEase(ease);
console.log(JSON.stringify({engine:'GSAP',version:gsap.version,ease,duration,samples:Array.from({length:samples},(_,i)=>({time:duration*i/(samples-1),progress:fn(i/(samples-1))}))}));
gsap.ticker.sleep();
