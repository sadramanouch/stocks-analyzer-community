import {Disclosure,WorkspaceHeading,WorkspaceNav} from '@/components/workspace-ui';
import BrokerConnection from "@/components/broker-connection";
import IbkrFlexImport from "@/components/ibkr-flex-import";

export default function BrokerPage(){
  return <main className="min-h-screen bg-zinc-950 text-zinc-100"><WorkspaceNav/><div className="workspace-page">
    <WorkspaceHeading eyebrow="Read-only connection" title="Broker reporting" description="Connect IBKR to review your statements. This connection cannot place trades."/>
    <BrokerConnection/><Disclosure title="Import statements manually"><IbkrFlexImport/></Disclosure>
  </div></main>;
}
