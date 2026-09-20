import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const read = (name: string) =>
  JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), 'utf8'));

describe('分发前提', () => {
  it('manifest 与 package.json 版本一致', () => {
    expect(read('manifest.json').version).toBe(read('package.json').version);
  });

  it('manifest 带固定扩展 ID 的公钥（缺了则 ID 随解压路径变化）', () => {
    expect(read('manifest.json').key).toMatch(/^[A-Za-z0-9+/]{100,}={0,2}$/);
  });
});
