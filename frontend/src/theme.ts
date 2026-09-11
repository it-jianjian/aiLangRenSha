/**
 * 全局设计令牌 + Ant Design 主题
 *
 * 视觉语言参考 werewolf_ui.html「月夜旅人」：深蓝夜色 + 月亮光晕 + 暖金主色 + 衬线大标题。
 * 一处令牌统一全站 antd 组件与自定义 CSS（CSS 变量见 index.css）。
 */

import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

/** 品牌色板（与 index.css 的 CSS 变量保持一致） */
export const brand = {
  bg: '#06111f',
  bgDeep: '#02060b',
  bgMid: '#07111f',
  panel: 'rgba(7, 18, 33, 0.86)',
  panel2: 'rgba(11, 26, 45, 0.92)',
  line: 'rgba(192, 220, 255, 0.18)',
  text: '#eef5ff',
  muted: '#9eb0c4',
  gold: '#f4cf87',
  gold2: '#b98539',
  blue: '#88c9ff',
  blue2: '#3e77aa',
  green: '#58d786',
  red: '#ff6b6b',
  violet: '#a78bfa',
} as const

export const antdThemeConfig: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: brand.gold,
    colorInfo: brand.blue,
    colorSuccess: brand.green,
    colorWarning: '#f0b45c',
    colorError: brand.red,
    colorLink: brand.gold,
    colorBgBase: brand.bg,
    colorTextBase: brand.text,
    colorBorder: 'rgba(192, 220, 255, 0.26)',
    colorBorderSecondary: brand.line,
    borderRadius: 12,
    borderRadiusLG: 16,
    borderRadiusSM: 8,
    fontFamily:
      "'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
    fontSize: 14,
    wireframe: false,
    controlHeight: 38,
  },
  components: {
    Button: {
      borderRadius: 12,
      controlHeight: 40,
      controlHeightLG: 48,
      fontWeight: 600,
      primaryShadow: '0 7px 20px rgba(232, 191, 121, 0.22)',
    },
    Card: {
      colorBgContainer: 'rgba(7, 18, 33, 0.86)',
      borderRadiusLG: 18,
    },
    Tag: {
      borderRadiusSM: 999,
    },
    Table: {
      colorBgContainer: 'transparent',
      headerBg: 'rgba(11, 26, 45, 0.6)',
      borderColor: brand.line,
      rowHoverBg: 'rgba(136, 201, 255, 0.06)',
    },
    Input: {
      colorBgContainer: 'rgba(7, 17, 29, 0.8)',
    },
    InputNumber: {
      colorBgContainer: 'rgba(7, 17, 29, 0.8)',
    },
    Segmented: {
      itemSelectedBg: 'linear-gradient(180deg, #f5d897, #b98236)',
      itemSelectedColor: '#23170a',
    },
    Modal: {
      contentBg: brand.bgMid,
      headerBg: brand.bgMid,
    },
    Tabs: {
      inkBarColor: brand.gold,
      itemSelectedColor: brand.gold,
      itemHoverColor: brand.text,
    },
  },
}
