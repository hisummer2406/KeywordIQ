import { theme } from 'antd';
import type { ReactNode } from 'react';

/** 没有 antd List 的简单分隔列表（几条静态展示，不需要虚拟列表） */
export default function Rows({
  children,
  bordered,
}: {
  children: ReactNode[];
  bordered?: boolean;
}) {
  const { token } = theme.useToken();
  return (
    <div
      style={
        bordered
          ? { border: `1px solid ${token.colorBorderSecondary}`, borderRadius: token.borderRadius }
          : undefined
      }
    >
      {children.map((c, i) => (
        <div
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          style={{
            padding: bordered ? '8px 16px' : '8px 0',
            borderTop: i ? `1px solid ${token.colorSplit}` : undefined,
          }}
        >
          {c}
        </div>
      ))}
    </div>
  );
}
