import {WorkspaceHeading,WorkspaceNav} from '@/components/workspace-ui';
import SystemStatus from '@/components/system-status';

export default function SystemPage() {
  return <main className="min-h-screen bg-zinc-950 text-zinc-100"><WorkspaceNav/><div className="workspace-page">
    <WorkspaceHeading eyebrow="Behind the scenes" title="System status" description="See which checks are running and which connections need attention."/>
    <SystemStatus/>
  </div></main>;
}
