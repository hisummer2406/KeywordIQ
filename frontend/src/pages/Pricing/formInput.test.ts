import { describe, expect, it } from 'vitest';
import { formatPct, pctToRate, rateToPct, roundDecimal, shiftDecimal } from '@/services/pricing';
import { DEFAULT_FORM_VALUES, fromPricingInput, sameInput, toPricingInput } from './formInput';

const CUR = { US: 'USD', GB: 'GBP', DE: 'EUR' };

describe('十进制字符串工具：不经过浮点', () => {
  it('百分数与比例互转', () => {
    expect(pctToRate('15')).toBe('0.15');
    expect(pctToRate('0.5')).toBe('0.005');
    expect(pctToRate('100')).toBe('1');
    expect(pctToRate('')).toBeNull();
    expect(rateToPct('0.155')).toBe('15.5');
    expect(rateToPct('0.07')).toBe('7');
    expect(shiftDecimal('0.1', -2)).toBe('10');
  });

  it('四舍五入在字符串上做（0.1+0.2 这类浮点误差不出现）', () => {
    expect(roundDecimal('93.745', 2)).toBe('93.75');
    expect(roundDecimal('9.995', 2)).toBe('10.00');
    expect(roundDecimal('-0.004', 2)).toBe('0.00');
    expect(formatPct('0.4')).toBe('40.00%');
    expect(formatPct(null)).toBe('—');
  });
});

describe('表单值 → 核价输入', () => {
  const base = { ...DEFAULT_FORM_VALUES, markets: ['US', 'GB'], purchase_cost: '10', category: 'general' };

  it('没填的市场项不发送（后端据此取市场默认值，而不是当成 0）', () => {
    const input = toPricingInput(
      { ...base, target_profit: '40', market_inputs: { US: { duty_pct: '0', ads_pct: '' }, GB: {} } },
      CUR,
    );
    expect(input.market_inputs).toEqual({ US: { duty_rate: '0' } });
  });

  it('目标利润率模式只发利润率，倒推采购价模式不发采购成本', () => {
    const m = toPricingInput({ ...base, mode: 'target_margin', target_profit: '40', target_margin_pct: '35' }, CUR);
    expect(m.target_margin).toBe('0.35');
    expect(m.target_profit).toBeNull();
    const p = toPricingInput(
      { ...base, mode: 'max_purchase', target_type: 'margin', target_margin_pct: '20', target_profit: '9' },
      CUR,
    );
    expect(p.purchase_cost).toBeNull();
    expect(p.target_margin).toBe('0.2');
    expect(p.target_profit).toBeNull();
  });

  it('调价与竞品价只保留已选市场，竞品币种取市场币种', () => {
    const input = toPricingInput(
      {
        ...base,
        markets: ['US'],
        competitor_prices: [
          { market: 'US', price: '19.99', includes_vat: false },
          { market: 'DE', price: '20' },
        ],
      },
      CUR,
      { 'US/FBA': '18.99', 'DE/FBA': '20' },
    );
    expect(input.price_overrides).toEqual({ 'US/FBA': '18.99' });
    expect(input.competitor_prices).toEqual([
      { market: 'US', price: '19.99', currency: 'USD', includes_vat: false, note: '' },
    ]);
  });

  it('不含液体属性时不发送容量', () => {
    const input = toPricingInput({ ...base, liquid_ml: '100', attributes: ['general'] }, CUR);
    expect(input.liquid_ml).toBeNull();
  });

  it('保存的输入能还原回表单并得到等价输入（结果不会被误判为过期）', () => {
    const v = {
      ...base,
      target_profit: '40',
      market_inputs: { US: { duty_pct: '25', price: '20' } },
    };
    const input = toPricingInput(v, CUR, { 'US/FBM:C1': '19.99' });
    const back = fromPricingInput(input);
    expect(sameInput(toPricingInput(back.values, CUR, back.priceOverrides), input)).toBe(true);
  });
});
