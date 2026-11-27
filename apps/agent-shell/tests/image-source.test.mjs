import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {imageMime,boundImage} from '../src/image-source.ts';
test('PNG/JPEG bytes determine MIME; an extension or declared MIME mismatch and a changed exact SHA are rejected',async()=>{
 const png=Uint8Array.from([137,80,78,71,13,10,26,10,0,0]),jpeg=Uint8Array.from([255,216,255,224,0,0,255,217]);const sha=bytes=>createHash('sha256').update(bytes).digest('hex');assert.equal(imageMime(png),'image/png');assert.equal(imageMime(jpeg),'image/jpeg');assert.equal((await boundImage(jpeg,'资料.JPEG',sha(jpeg))).mime,'image/jpeg');assert.equal((await boundImage(png,'原件.png',sha(png))).mime,'image/png');await assert.rejects(()=>boundImage(png,'misnamed.jpg',sha(png)),/格式/);await assert.rejects(()=>boundImage(jpeg,'misnamed.png',sha(jpeg)),/格式/);await assert.rejects(()=>boundImage(jpeg,'actual.jpg',sha(jpeg),'image/png'),/MIME/);await assert.rejects(()=>boundImage(jpeg,'actual.jpg','0'.repeat(64)),/SHA/);assert.throws(()=>imageMime(Uint8Array.from([255,216])),/签名/);
 // Signature admission is not image decoding. The browser createImageBitmap and
 // actual backend decoder separately reject malformed/truncated image content.
});
