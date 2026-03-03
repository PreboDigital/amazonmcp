export function getAccountScopeMeta(account) {
  const accountType = (account?.account_type || '').toLowerCase()
  const isCredentialPlaceholder = Boolean(account?._isCred || accountType === 'credential')
  const hasMarketplaceProfile = Boolean(account?.profile_id && account?.marketplace)
  const isGlobalRoot = accountType === 'global' && !hasMarketplaceProfile
  const canSyncCampaigns = hasMarketplaceProfile

  let statusLabel = 'Unscoped'
  let warning = ''

  if (hasMarketplaceProfile) {
    statusLabel = 'Marketplace profile'
  } else if (isGlobalRoot) {
    statusLabel = 'Global root'
    warning = 'This selection is a global/root advertiser account. Switch to a single marketplace child profile like US or GB before syncing campaigns.'
  } else if (isCredentialPlaceholder) {
    statusLabel = 'Credentials only'
    warning = 'Discover accounts and select a marketplace profile before syncing campaigns.'
  } else if (account?.profile_id && !account?.marketplace) {
    statusLabel = 'Profile only'
    warning = 'This account is missing marketplace context. Re-discover accounts and select a marketplace child profile before syncing campaigns.'
  } else {
    warning = 'Select a marketplace child profile before syncing campaigns.'
  }

  return {
    isCredentialPlaceholder,
    hasMarketplaceProfile,
    isGlobalRoot,
    canSyncCampaigns,
    statusLabel,
    warning,
  }
}
