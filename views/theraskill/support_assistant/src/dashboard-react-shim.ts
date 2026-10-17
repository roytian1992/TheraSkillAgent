type HermesSDK = { React: Record<string, any> }

const sdk = (window as unknown as { __HERMES_PLUGIN_SDK__?: HermesSDK }).__HERMES_PLUGIN_SDK__
if (!sdk?.React) throw new Error('Hermes Dashboard SDK is unavailable')

const React = sdk.React
export default React
export const Fragment = React.Fragment
export const createElement = React.createElement
export const createContext = React.createContext
export const forwardRef = React.forwardRef
export const memo = React.memo
export const useCallback = React.useCallback
export const useContext = React.useContext
export const useEffect = React.useEffect
export const useLayoutEffect = React.useLayoutEffect
export const useMemo = React.useMemo
export const useRef = React.useRef
export const useState = React.useState
export const useReducer = React.useReducer
export const useId = React.useId
export const jsx = (type: any, props: any, key?: string) => React.createElement(type, { ...props, ...(key ? { key } : {}) })
export const jsxs = jsx
