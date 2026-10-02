<template>
  <section class="page" data-module="transmission">
    <header class="page-head">
      <div>
        <h2>传输设备管理</h2>
        <p class="page-desc">维护传输设备，围绕设备编号、传输类型、带宽容量、所属站点做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记传输设备</button>
        <button class="btn" type="button" @click="exportRows">导出传输设备清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">误码率阈值（第 {{ threshold.version }} 版）</span>
        <strong class="stat-value">{{ formatBer(threshold.value) }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="keyword" placeholder="按设备编号检索" />
      </label>
      <label class="filter-item">
        <span>设备状态</span>
        <select v-model="status">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>告警名单</span>
        <label class="check-item"><input v-model="alarmOnly" type="checkbox" /> 只看告警</label>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <form class="filter-bar" @submit.prevent="updateThreshold">
      <label class="filter-item">
        <span>调整误码率阈值</span>
        <input v-model="thresholdInput" placeholder="如 1E-4" />
      </label>
      <button class="btn" type="submit">保存并重判存量设备</button>
      <span class="hint-text">保存后存量设备按新阈值重判，历史结论仍照当时那一版</span>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>
              <span class="status-tag" :data-status="row.status">{{ row.status }}</span>
            </td>
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
              <button class="link" type="button" @click="toggleHistory(row)">
                {{ expandedId === row.id ? '收起记录' : '判定记录' }}
              </button>
            </td>
          </tr>
          <tr v-if="expandedId === row.id">
            <td :colspan="columns.length + 2">
              <table class="data-table history-table">
                <thead>
                  <tr><th>时间</th><th>原因</th><th>误码率</th><th>阈值（版本）</th><th>结论</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(item, index) in historyRows" :key="index">
                    <td>{{ item.at }}</td>
                    <td>{{ item.reason }}</td>
                    <td>{{ item['误码率'] ?? '—' }}</td>
                    <td>{{ formatBer(item.threshold) }}（第 {{ item.threshold_version }} 版）</td>
                    <td>{{ item.conclusion }}</td>
                  </tr>
                  <tr v-if="!historyRows.length">
                    <td colspan="5" class="empty-state">暂无判定记录</td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无传输设备数据，可先登记传输设备</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条传输设备记录，第 {{ page }} / {{ pageCount }} 页</span>
      <span class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="btn ghost" type="button" :disabled="page >= pageCount" @click="goPage(page + 1)">下一页</button>
        <select v-model.number="size" @change="applyFilters">
          <option :value="10">每页 10 条</option>
          <option :value="20">每页 20 条</option>
          <option :value="50">每页 50 条</option>
        </select>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="hint-text">{{ noticeMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/transmission'
const columns = ["设备编号", "传输类型", "带宽容量", "所属站点", "光口状态", "电口状态", "误码率", "设备状态"]
const actions = ["记录告警", "记录误码", "安排修复"]
const statuses = ["正常", "光口告警", "误码超标", "已修复"]

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const keyword = ref('')
const status = ref('')
const alarmOnly = ref(false)
const stats = ref([{"label": "正常传输", "value": 0}, {"label": "告警传输", "value": 0}, {"label": "误码超标传输", "value": 0}])
const threshold = ref({ version: 1, value: '' })
const thresholdInput = ref('')
const expandedId = ref<string | number | null>(null)
const historyRows = ref<Row[]>([])
const errorMessage = ref('')
const noticeMessage = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

function formatBer(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return String(value ?? '—')
  const [mantissa, exponent] = num.toExponential(1).split('e')
  return `${mantissa}E${Number(exponent)}`
}

function currentQuery(): URLSearchParams {
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (status.value) query.set('status', status.value)
  if (alarmOnly.value) query.set('alarm_only', 'true')
  return query
}

function resetFilters() {
  keyword.value = ''
  status.value = ''
  alarmOnly.value = false
  page.value = 1
  void reload()
}

function applyFilters() {
  page.value = 1
  void reload()
}

function goPage(target: number) {
  page.value = Math.min(Math.max(target, 1), pageCount.value)
  void reload()
}

function exportRows() {
  // 导出与页面同一份条件：后端按当前条件生成一份 CSV 文件，行数与页面总数一致
  window.open(`${ENDPOINT}/export?${currentQuery().toString()}`, '_blank')
}

function openCreate() {
  errorMessage.value = '传输设备登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '记录误码') {
    const ber = window.prompt('请输入本次测得的误码率（如 3E-4）')
    if (ber === null) return
    values['误码率'] = ber
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '传输设备动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传输设备操作失败'
  }
}

async function toggleHistory(row: Row) {
  if (expandedId.value === row.id) {
    expandedId.value = null
    historyRows.value = []
    return
  }
  expandedId.value = row.id ?? null
  try {
    const response = await request(`${ENDPOINT}/${row.id}/history`)
    if (!response.ok) throw new Error('判定记录读取失败')
    historyRows.value = await response.json()
  } catch (error) {
    historyRows.value = []
    errorMessage.value = error instanceof Error ? error.message : '判定记录读取失败'
  }
}

async function updateThreshold() {
  errorMessage.value = ''
  noticeMessage.value = ''
  const value = Number(thresholdInput.value)
  if (!Number.isFinite(value) || value <= 0) {
    errorMessage.value = '阈值需要是大于 0 的数值（如 1E-4）'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/threshold`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail ?? '阈值保存失败')
    noticeMessage.value = payload.message ?? ''
    thresholdInput.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '阈值保存失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = currentQuery()
  query.set('page', String(page.value))
  query.set('size', String(size.value))
  try {
    const [listResp, statsResp, thresholdResp] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
      request(`${ENDPOINT}/threshold`),
    ])
    if (!listResp.ok) throw new Error('传输设备列表读取失败')
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (statsResp.ok) stats.value = await statsResp.json()
    if (thresholdResp.ok) threshold.value = await thresholdResp.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '传输设备列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.pager { display: inline-flex; gap: 8px; align-items: center; }
.check-item { display: inline-flex; gap: 4px; align-items: center; font-size: 13px; }
.hint-text { color: var(--muted); font-size: 12px; }
.status-tag { padding: 2px 8px; border-radius: 10px; background: #eef2f7; }
.status-tag[data-status='误码超标'] { background: #fee4e2; color: #b42318; }
.status-tag[data-status='光口告警'] { background: #fef0c7; color: #b54708; }
.status-tag[data-status='已修复'] { background: #dcfae6; color: #067647; }
.history-table { margin: 4px 0; }
</style>
