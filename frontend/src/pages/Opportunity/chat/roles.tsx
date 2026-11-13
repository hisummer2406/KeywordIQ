import type { BubbleListProps } from '@ant-design/x';
import { Avatar, Tag, theme } from 'antd';
import type { CSSProperties, ReactNode } from 'react';
import { EXPERT_BY_KEY, type ExpertKey } from '../experts';
import './chat.less';

/**
 * 机会中心两处对话共用的消息规范（change refine-opportunity-chat-ui 决策 D1）：
 * - user：右侧浅色填充气泡，用户自己的输入；
 * - ai：左侧无边框正文 + 专家头像，AI 的回答、方案与分析；
 * - event：左侧无头像的紧凑事件行，缩进到 AI 正文位置，检索、取证、签署、失败等任务事件。
 * 模型名、时间、操作人一律放在气泡底部的 MetaLine，不进标题。
 */

/** 头像宽度 + 间距：事件行据此对齐 AI 正文 */
const AVATAR = 32;
const AVATAR_GAP = 12;

export function ExpertAvatar({ expert, size = AVATAR }: { expert: ExpertKey; size?: number }) {
  const e = EXPERT_BY_KEY[expert];
  return (
    <Avatar size={size} style={{ background: e.color, flex: 'none', fontSize: size * 0.4 }}>
      {e.initial}
    </Avatar>
  );
}

/** 用户气泡的样式：浅主色填充、正文换行保留、最多占 80% */
export function useUserBubbleStyles(): Partial<Record<'content', CSSProperties>> {
  const { token } = theme.useToken();
  return {
    content: {
      background: token.colorPrimaryBg,
      color: token.colorText,
      maxWidth: '80%',
      whiteSpace: 'pre-wrap',
      overflowWrap: 'anywhere',
    },
  };
}

/** Bubble.List 的三种角色 */
export function useChatRoles(expert: ExpertKey): NonNullable<BubbleListProps['role']> {
  const user = useUserBubbleStyles();
  return {
    user: {
      placement: 'end',
      variant: 'filled',
      footerPlacement: 'outer-end',
      styles: user,
    },
    ai: {
      placement: 'start',
      variant: 'borderless',
      footerPlacement: 'outer-start',
      avatar: <ExpertAvatar expert={expert} />,
      styles: {
        body: { flex: 1, minWidth: 0 },
        content: { width: '100%', padding: 0, overflowWrap: 'anywhere' },
      },
    },
    event: {
      placement: 'start',
      variant: 'borderless',
      styles: {
        root: { paddingInlineStart: AVATAR + AVATAR_GAP },
        body: { flex: 1, minWidth: 0 },
        content: { width: '100%', padding: 0, minHeight: 0 },
      },
    },
  };
}

/** 气泡标题：只放「这是什么」，12 号三级文字 */
export function BubbleTitle({ children }: { children: ReactNode }) {
  return <div className="chat-meta">{children}</div>;
}

/** 弱化的元信息：模型名、时间、操作人等，空值自动跳过 */
export function MetaLine({ parts }: { parts: (ReactNode | null | undefined | false)[] }) {
  const shown = parts.filter((p) => p !== null && p !== undefined && p !== false && p !== '');
  if (!shown.length) return null;
  return (
    <div className="chat-meta">
      {shown.map((p, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <span key={i}>
          {i > 0 && ' · '}
          {p}
        </span>
      ))}
    </div>
  );
}

/** 引用：回答底部的一行标签 */
export function CitationTags({ labels }: { labels: string[] }) {
  if (!labels.length) return null;
  return (
    <div className="chat-meta" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
      <span>引用：</span>
      {labels.map((l) => (
        <Tag key={l} style={{ marginInlineEnd: 0 }}>
          {l}
        </Tag>
      ))}
    </div>
  );
}
