const ROLE_STYLES = {
  pm: { bg: '#F3E8FF', color: '#8B5CF6', label: 'PM' },
  developer: { bg: '#E0F2FE', color: '#3B82F6', label: 'Dev' },
  devops: { bg: '#DCFCE7', color: '#10B981', label: 'Ops' },
  qa: { bg: '#FEF3C7', color: '#F59E0B', label: 'QA' },
  designer: { bg: '#FCE7F3', color: '#EC4899', label: 'Design' },
  ux: { bg: '#D1FAE5', color: '#059669', label: 'UX' },
  finance_manager: { bg: '#D1FAE5', color: '#059669', label: 'Finance' },
  accountant: { bg: '#ECFDF5', color: '#10B981', label: 'Acct' },
  investor_relations: { bg: '#FEF3C7', color: '#D97706', label: 'IR' },
  treasury_analyst: { bg: '#FEF3C7', color: '#D97706', label: 'Treasury' },
  ops_manager: { bg: '#FFEDD5', color: '#EA580C', label: 'Ops' },
  verification_specialist: { bg: '#FFF7ED', color: '#C2410C', label: 'Verify' },
  dispute_manager: { bg: '#FEE2E2', color: '#DC2626', label: 'Dispute' },
  customer_support: { bg: '#E0E7FF', color: '#4F46E5', label: 'Support' },
  data_scientist: { bg: '#EDE9FE', color: '#7C3AED', label: 'Data Sci' },
  ml_engineer: { bg: '#F3E8FF', color: '#9333EA', label: 'ML' },
  risk_analyst: { bg: '#FEF2F2', color: '#B91C1C', label: 'Risk' },
  analytics_engineer: { bg: '#E0F2FE', color: '#0284C7', label: 'Analytics' },
  marketing_manager: { bg: '#FDF2F8', color: '#DB2777', label: 'Mktg' },
  content_creator: { bg: '#FFF1F2', color: '#E11D48', label: 'Content' },
  b2b_sales: { bg: '#F0FDF4', color: '#16A34A', label: 'Sales' },
  customer_success: { bg: '#ECFEFF', color: '#0891B2', label: 'CS' },
  hr_manager: { bg: '#F5F3FF', color: '#6D28D9', label: 'HR' },
  recruiter: { bg: '#F3E8FF', color: '#7C3AED', label: 'Recruit' },
  benefits_admin: { bg: '#F0F9FF', color: '#0369A1', label: 'Benefits' },
  training_coordinator: { bg: '#F0FDF4', color: '#15803D', label: 'Training' },
  corporate_lawyer: { bg: '#FEF2F2', color: '#991B1B', label: 'Legal' },
  compliance_officer: { bg: '#FFFBEB', color: '#B45309', label: 'Comply' },
  contract_manager: { bg: '#FEF3C7', color: '#B45309', label: 'Contracts' },
  admin_manager: { bg: '#F0F9FF', color: '#0E7490', label: 'Admin' },
  euc_specialist: { bg: '#ECFEFF', color: '#0891B2', label: 'EUC' },
  csr_coordinator: { bg: '#F0FDF4', color: '#15803D', label: 'CSR' },
  office_manager: { bg: '#F5F5F4', color: '#57534E', label: 'Office' },
};

export default function AgentPanel({ agents = [], onAgentClick }) {
  return (
    <div style={{ background: 'white', borderRadius: 16, border: '1px solid #eee', padding: 20 }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 16px 0', textTransform: 'uppercase', letterSpacing: '0.5px', color: '#2D2D2D' }}>
        Agents
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {agents.map(a => {
          const style = ROLE_STYLES[a.role] || ROLE_STYLES.developer;
          return (
            <button
              key={a.id}
              onClick={() => onAgentClick?.(a)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 12px', borderRadius: 10, border: '1px solid',
                borderColor: a.busy ? style.color + '40' : '#f0f0f0',
                background: a.busy ? style.bg : '#FAFAFA',
                cursor: 'pointer', textAlign: 'left', width: '100%',
                transition: 'all 0.15s', fontFamily: 'inherit',
              }}
            >
              <div style={{
                width: 30, height: 30, borderRadius: 8,
                background: style.bg, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                fontSize: 10, fontWeight: 700, color: style.color,
                flexShrink: 0,
              }}>
                {style.label}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#2D2D2D' }}>{a.name}</div>
                <div style={{ fontSize: 11, color: '#999' }}>
                  {a.busy ? (a.current_task?.action || 'Working...') : 'Available'}
                </div>
              </div>
              <div style={{ fontSize: 11, color: '#999', textAlign: 'right', whiteSpace: 'nowrap' }}>
                <div>{a.tasks_completed} done</div>
                <div style={{ color: a.success_rate > 0.8 ? '#10B981' : '#F59E0B' }}>
                  {Math.round(a.success_rate * 100)}%
                </div>
              </div>
              <div style={{
                width: 8, height: 8, borderRadius: 4,
                background: a.busy ? '#10B981' : '#94A3B8',
                flexShrink: 0,
              }} />
              <span style={{ fontSize: 14, color: '#bbb', marginLeft: 4 }}>💬</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
