import { beforeEach, describe, expect, it } from 'vitest';
import { getApiBaseUrl, getAuthToken, setApiBaseUrl, setAuthToken } from '../src/auth';
import { installChromeStub } from './chrome-stub';

describe('auth', () => {
  beforeEach(() => {
    installChromeStub();
  });

  it('未设置时 getAuthToken 返回 null', async () => {
    expect(await getAuthToken()).toBeNull();
  });

  it('设置后能取回', async () => {
    await setAuthToken('abc.def.ghi');
    expect(await getAuthToken()).toBe('abc.def.ghi');
  });

  it('未设置时 getApiBaseUrl 返回 null', async () => {
    expect(await getApiBaseUrl()).toBeNull();
  });

  it('设置后能取回，且去掉结尾多余的斜杠', async () => {
    await setApiBaseUrl('http://localhost:8000/');
    expect(await getApiBaseUrl()).toBe('http://localhost:8000');
  });
});
