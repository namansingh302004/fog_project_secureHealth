import { useState, useEffect } from 'react';
import { Navbar, Container } from 'react-bootstrap';
import { FaHeartbeat } from 'react-icons/fa';

const Clock = () => {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const timerId = setInterval(() => {
      setTime(new Date());
    }, 1000);
    return () => clearInterval(timerId);
  }, []);

  return <div className="header-time">{time.toLocaleTimeString('en-GB')}</div>;
};

interface HeaderProps {
  cloudOnline: boolean;
  fogOnline: boolean;
}

const Header = ({ cloudOnline, fogOnline }: HeaderProps) => {
  return (
    <Navbar bg="dark" variant="dark" sticky="top" className="header">
      <Container fluid>
        <Navbar.Brand href="#home" className="logo">
          <FaHeartbeat size={36} className="logo-icon" />
          <div>
            <div className="logo-text">SecureHealth</div>
            <div className="logo-sub">Fog Computing · Cardiac Monitor</div>
          </div>
        </Navbar.Brand>
        <div className="status-bar">
          <div className="status-item"><div className={`status-dot ${!fogOnline ? 'offline' : ''}`} id="dot-edge"></div> EDGE</div>
          <div className="status-item"><div className={`status-dot ${!fogOnline ? 'offline' : ''}`} id="dot-fog"></div> FOG</div>
          <div className="status-item"><div className={`status-dot ${!cloudOnline ? 'offline' : ''}`} id="dot-cloud"></div> CLOUD</div>
          <div className="status-item">AES-256 ✓ HMAC-SHA256 ✓</div>
        </div>
        <Clock />
      </Container>
    </Navbar>
  );
};

export default Header;
