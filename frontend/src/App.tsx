import { Container } from 'react-bootstrap';
import Header from './components/Header';
import Footer from './components/Footer';
import StatsRow from './components/StatsRow';
import AlertFeed from './components/AlertFeed';
import DistributionChart from './components/DistributionChart';
import LatencyChart from './components/LatencyChart';
import SystemLog from './components/SystemLog';
import EcgChart from './components/EcgChart';
import { useDataFetching } from './hooks/useDataFetching';
import './styles/App.scss';

function App() {
  const { alerts, stats, fogStats, cloudOnline, fogOnline } = useDataFetching();

  return (
    <>
      <Header cloudOnline={cloudOnline} fogOnline={fogOnline} />

      <Container fluid className="main-grid">
        <EcgChart alerts={alerts} />

        <StatsRow alerts={alerts} stats={stats} />

        <AlertFeed alerts={alerts} />

        <DistributionChart alerts={alerts} fogStats={fogStats} />

        <LatencyChart alerts={alerts} />

        <SystemLog fogStats={fogStats} />
      </Container>

      <Footer />
    </>
  )
}

export default App;
