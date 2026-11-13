import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './chat.less';

/**
 * 大模型「回答」的受限 Markdown（change refine-opportunity-chat-ui 决策 D2；admin-spa「对话类界面的组件与安全边界」）。
 *
 * 只用于对话回答；检索方案、风险分析与供应商文本仍按纯文本。三道限制：
 * 1. 在语法树上降级：原始 HTML 变成字面文本，链接只留文字，图片只留替代文字；
 * 2. 元素白名单（没有 a、img），名单外的元素拆掉外壳只留内容；
 * 3. 所有地址一律置空。
 * 不解析 HTML（没有 rehype-raw），也不用 dangerouslySetInnerHTML。
 */

/** mdast 节点的最小形状：这里只按 type 改写，不依赖 @types/mdast */
interface MdNode {
  type: string;
  value?: string;
  alt?: string | null;
  label?: string | null;
  identifier?: string;
  children?: MdNode[];
}

/** 这些容器里的子节点是块级的：HTML 块改成文本时要包一层段落 */
const BLOCK_PARENTS = new Set(['root', 'blockquote', 'listItem', 'footnoteDefinition']);

function defang(parent: MdNode): void {
  if (!parent.children) return;
  const block = BLOCK_PARENTS.has(parent.type);
  parent.children = parent.children.flatMap((child): MdNode[] => {
    switch (child.type) {
      case 'html': {
        const text: MdNode = { type: 'text', value: child.value ?? '' };
        return [block ? { type: 'paragraph', children: [text] } : text];
      }
      case 'link':
      case 'linkReference':
        defang(child);
        return child.children ?? [];
      case 'image':
      case 'imageReference':
        return child.alt ? [{ type: 'text', value: child.alt }] : [];
      case 'footnoteReference':
        return [{ type: 'text', value: `[^${child.label ?? child.identifier ?? ''}]` }];
      case 'footnoteDefinition':
      case 'definition':
        // 脚注与链接定义会生成带锚点链接的区块；回答里用不上，丢掉定义行
        return [];
      default:
        defang(child);
        return [child];
    }
  });
}

/** remark 插件：在转成 HTML 语法树之前做降级 */
export function remarkDefang() {
  return (tree: MdNode) => defang(tree);
}

export const ALLOWED_ELEMENTS = [
  'p',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'strong',
  'em',
  'del',
  'code',
  'pre',
  'blockquote',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'hr',
  'br',
];

const noUrl = () => '';

export default function AnswerMarkdown({ children }: { children: string }) {
  return (
    <div className="chat-answer">
      <Markdown
        remarkPlugins={[remarkGfm, remarkDefang]}
        allowedElements={ALLOWED_ELEMENTS}
        unwrapDisallowed
        urlTransform={noUrl}
        components={{
          // 表格过宽时在回答内部横向滚动，不撑破气泡和页面
          table: ({ children }) => (
            <div className="chat-answer__table">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {children}
      </Markdown>
    </div>
  );
}
