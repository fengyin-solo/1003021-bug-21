<template>
  <section class="page" data-module="transmission">
    <header class="page-head">
      <div>
        <h2>传输设备管理</h2>
        <p class="page-desc">误码率按阈值统一判定：过线先记误码超标，修复后移出告警名单；列表、统计、告警名单、导出同一口径。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showCreate = !showCreate">登记传输设备</button>
        <button class="btn" type="button" @click="exportRows">导出传输设备清单</button>
      </div>
    </header>

    <div class="threshold-bar">
      <span>
        当前误码率阈值：<strong>{{ threshold?.['误码率阈值'] ?? '—' }}</strong>
        <em>（第 {{ threshold?.['版本'] ?? '—' }} 版，{{ threshold?.['生效时间'] ?? '—' }} 起生效）</em>
      </span>
      <input v-model="thresholdInput" placeholder="新阈值，如 1e-6" />
      <button class="btn" type="button" @click="updateThreshold">更新阈值并重判存量</button>
      <span class="hint">改阈值后存量设备按新版重判，历史结论保留当时版本</span>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="alert-panel">
      <h3>告警名单（{{ alertTotal }} 台，与设备列表同口径）</h3>
      <table v-if="alertRows.length" class="data-table">
        <thead>
          <tr>
            <th>设备编号</th>
            <th>传输类型</th>
            <th>所属站点</th>
            <th>误码率</th>
            <th>当前状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in alertRows" :key="String(row.id)">
            <td>{{ row['设备编号'] }}</td>
            <td>{{ row['传输类型'] }}</td>
            <td>{{ row['所属站点'] }}</td>
            <td>{{ row['误码率'] }}</td>
            <td>{{ row['设备状态'] }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">当前条件下没有告警设备</p>
    </section>

    <form class="filter-bar" @submit.prevent="search">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="filters.keyword" placeholder="按设备编号检索" />
      </label>
      <label class="filter-item">
        <span>传输类型</span>
        <input v-model="filters['传输类型']" placeholder="按传输类型检索" />
      </label>
      <label class="filter-item">
        <span>带宽容量</span>
        <input v-model="filters['带宽容量']" placeholder="按带宽容量检索" />
      </label>
      <label class="filter-item">
        <span>当前状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <form v-if="showCreate" class="create-panel" @submit.prevent="createRow">
      <label v-for="field in createFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
      <span class="hint">误码率过线将直接记为误码超标，不会落成正常</span>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="showHistory(row)">判定历史</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无传输设备数据，可先登记传输设备</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条传输设备记录</span>
      <div class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="gotoPage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ pageCount }} 页</span>
        <button class="btn ghost" type="button" :disabled="page >= pageCount" @click="gotoPage(page + 1)">下一页</button>
        <select v-model.number="size" @change="changeSize">
          <option :value="10">10 条/页</option>
          <option :value="20">20 条/页</option>
          <option :value="50">50 条/页</option>
        </select>
      </div>
    </footer>

    <p v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</p>
    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <section v-if="historyRow" class="history-panel">
      <h3>{{ historyRow['设备编号'] }} 的判定历史（历史结论按当时阈值版本保留）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>事由</th>
            <th>阈值版本</th>
            <th>当时阈值</th>
            <th>误码率</th>
            <th>结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in historyList" :key="index">
            <td>{{ item.时间 }}</td>
            <td>{{ item.事由 }}</td>
            <td>第 {{ item.版本 }} 版</td>
            <td>{{ item.阈值 }}</td>
            <td>{{ item.误码率 ?? '—' }}</td>
            <td>{{ item.结论 }}</td>
          </tr>
        </tbody>
      </table>
      <button class="btn ghost" type="button" @click="historyRow = null">收起</button>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

interface Judgment {
  版本: number
  阈值: number
  误码率: number | null
  结论: string
  事由: string
  时间: string
}

const ENDPOINT = '/api/transmission'
const columns = ["设备编号", "传输类型", "带宽容量", "所属站点", "光口状态", "电口状态", "误码率", "设备状态"]
const actions = ["记录告警", "记录误码", "安排修复"]
const statuses = ["正常", "光口告警", "误码超标", "已修复"]
const createFields = ["设备编号", "传输类型", "带宽容量", "所属站点", "光口状态", "电口状态", "误码率"]

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

const filters = ref<Record<string, string>>({ keyword: '', 传输类型: '', 带宽容量: '', status: '' })

const summary = ref<Record<string, any>>({ by_status: {}, alerts: 0 })
const stats = computed(() => {
  const byStatus = summary.value?.by_status ?? {}
  return [
    { label: '正常传输', value: byStatus['正常'] ?? 0 },
    { label: '光口告警', value: byStatus['光口告警'] ?? 0 },
    { label: '误码超标', value: byStatus['误码超标'] ?? 0 },
    { label: '已修复', value: byStatus['已修复'] ?? 0 },
    { label: '告警合计', value: summary.value?.alerts ?? 0 },
  ]
})

const alertRows = ref<Row[]>([])
const alertTotal = ref(0)

const threshold = ref<Record<string, any> | null>(null)
const thresholdInput = ref('')

const errorMessage = ref('')
const noticeMessage = ref('')

const showCreate = ref(false)
const emptyCreate = () => ({ 设备编号: '', 传输类型: '', 带宽容量: '', 所属站点: '', 光口状态: '正常', 电口状态: '正常', 误码率: '' })
const createForm = ref<Record<string, string>>(emptyCreate())

const historyRow = ref<Row | null>(null)
const historyList = computed<Judgment[]>(() => (historyRow.value?.['判定历史'] as Judgment[] | undefined) ?? [])

function queryString(extra: Record<string, string | number> = {}): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params.set(key, value)
  }
  for (const [key, value] of Object.entries(extra)) {
    params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

async function readPayload(response: Response) {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : `接口返回 ${response.status}，操作未生效`)
  }
  return payload
}

async function loadList() {
  const payload = await readPayload(await request(`${ENDPOINT}${queryString({ page: page.value, size: size.value })}`))
  rows.value = payload.items ?? []
  total.value = payload.total ?? 0
}

async function loadSummary() {
  const payload = await readPayload(await request(`${ENDPOINT}/summary${queryString()}`))
  summary.value = payload
  threshold.value = payload?.threshold ?? threshold.value
}

async function loadAlerts() {
  const payload = await readPayload(await request(`${ENDPOINT}/alerts${queryString()}`))
  alertRows.value = payload.items ?? []
  alertTotal.value = payload.total ?? 0
}

async function reload() {
  errorMessage.value = ''
  try {
    await Promise.all([loadList(), loadSummary(), loadAlerts()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传输设备数据读取失败'
  }
}

function search() {
  page.value = 1
  void reload()
}

function resetFilters() {
  filters.value = { keyword: '', 传输类型: '', 带宽容量: '', status: '' }
  page.value = 1
  void reload()
}

function gotoPage(next: number) {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  void reload()
}

function changeSize() {
  page.value = 1
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export${queryString()}`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  const values: Record<string, any> = { action }
  if (action === '记录误码') {
    const input = window.prompt(`录入 ${row['设备编号']} 的误码率（数值，如 8e-6）`, String(row['误码率'] ?? ''))
    if (input === null) return
    values['误码率'] = input
  }
  try {
    const payload = await readPayload(await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    }))
    if (payload.ok) {
      noticeMessage.value = payload.message
    } else {
      errorMessage.value = payload.message
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传输设备操作失败'
  }
}

async function createRow() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const payload = await readPayload(await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    }))
    if (!payload.ok) {
      errorMessage.value = payload.message
      return
    }
    noticeMessage.value = payload.message
    showCreate.value = false
    createForm.value = emptyCreate()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传输设备登记失败'
  }
}

async function updateThreshold() {
  errorMessage.value = ''
  noticeMessage.value = ''
  if (!thresholdInput.value.trim()) {
    errorMessage.value = '请先填写新的误码率阈值'
    return
  }
  try {
    const payload = await readPayload(await request(`${ENDPOINT}/threshold`, {
      method: 'PUT',
      body: JSON.stringify({ values: { 误码率阈值: thresholdInput.value.trim() } }),
    }))
    if (!payload.ok) {
      errorMessage.value = payload.message
      return
    }
    noticeMessage.value = payload.message
    thresholdInput.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '阈值更新失败'
  }
}

async function showHistory(row: Row) {
  errorMessage.value = ''
  try {
    historyRow.value = await readPayload(await request(`${ENDPOINT}/${row.id}`))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '判定历史读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.threshold-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.threshold-bar input {
  width: 150px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.threshold-bar em {
  color: var(--muted);
  font-style: normal;
}
.hint {
  color: var(--muted);
  font-size: 12px;
}
.alert-panel,
.history-panel,
.create-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.alert-panel h3,
.history-panel h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.create-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}
.filter-item input,
.filter-item select,
.pager select {
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
}
.pager {
  display: flex;
  gap: 8px;
  align-items: center;
}
.notice-text {
  color: #067647;
  font-size: 13px;
}
.history-panel .btn {
  margin-top: 8px;
}
</style>
