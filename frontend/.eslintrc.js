module.exports = {
  extends: require.resolve('@umijs/max/eslint'),
  rules: {
    // 页内单选切换统一用实心按钮组（Radio.Group optionType="button" buttonStyle="solid"），
    // 不用 Segmented——2026-11-10 用户要求全系统同一风格与交互，见 .claude/rules/frontend.md。
    'no-restricted-imports': [
      'error',
      {
        paths: [
          {
            name: 'antd',
            importNames: ['Segmented'],
            message:
              '页内单选切换用 <Radio.Group optionType="button" buttonStyle="solid">，不用 Segmented（全系统统一风格）。',
          },
        ],
      },
    ],
  },
};
