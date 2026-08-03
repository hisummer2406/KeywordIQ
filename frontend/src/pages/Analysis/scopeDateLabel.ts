import type { View } from '@/services/analysis';

const DAY_MS = 24 * 60 * 60 * 1000;

function parseDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;

  const [, year, month, day] = match;
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return parsed.toISOString().slice(0, 10) === value ? parsed : null;
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

function monthDay(value: Date): string {
  return `${pad(value.getUTCMonth() + 1)}/${pad(value.getUTCDate())}`;
}

export function formatScopeDateLabel(value: string, view: View): string {
  const date = parseDate(value);
  if (!date) return value;

  const year = date.getUTCFullYear();
  if (view === 'DAY') {
    return `${year}年${pad(date.getUTCMonth() + 1)}月${pad(date.getUTCDate())}日`;
  }

  const firstDay = new Date(Date.UTC(year, 0, 1));
  const dayOfYear = Math.floor((date.getTime() - firstDay.getTime()) / DAY_MS) + 1;
  const week = Math.floor((dayOfYear + firstDay.getUTCDay() - 1) / 7) + 1;
  const start = new Date(date);
  start.setUTCDate(date.getUTCDate() - date.getUTCDay());
  const end = new Date(start);
  end.setUTCDate(start.getUTCDate() + 6);

  return `${year}年第${week}周（${monthDay(start)}–${monthDay(end)}）`;
}
