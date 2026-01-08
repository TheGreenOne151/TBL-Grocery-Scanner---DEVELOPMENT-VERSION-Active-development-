"""
URL Monitoring Script for TBL App
Checks if certification database URLs are still accessible
Run weekly to catch broken links: python url_monitoring_script.py
"""

import httpx
import json
from datetime import datetime
from typing import Dict, List

# Certification sources - MUST MATCH app.py
CERT_SOURCES = {
    "b_corp": "https://www.bcorporation.net/en-us/find-a-b-corp",
    "fair_trade": "https://www.fairtrade.net/en.html",
    "carbon_trust": "https://www.carbontrust.com/our-clients/",
    "cradle_to_cradle": "https://www.c2ccertified.org/certified-products",
    "leaping_bunny": "https://www.leapingbunny.org/shopping-guide",
    "green_seal": "https://certified.greenseal.org/directory"
}

def check_url_health(url: str, timeout: int = 10) -> Dict:
    """Check if a URL is accessible and return status"""
    try:
        response = httpx.head(url, timeout=timeout, follow_redirects=True)
        return {
            "url": url,
            "status": response.status_code,
            "accessible": response.status_code < 400,
            "timestamp": datetime.now().isoformat()
        }
    except httpx.TimeoutException:
        return {
            "url": url,
            "status": "TIMEOUT",
            "accessible": False,
            "error": "Request timed out",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "url": url,
            "status": "ERROR",
            "accessible": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

def monitor_all_sources() -> Dict:
    """Check all certification sources and return report"""
    results = {
        "checked_at": datetime.now().isoformat(),
        "total_sources": len(CERT_SOURCES),
        "status_by_source": {},
        "all_healthy": True,
        "broken_links": []
    }

    print("🔍 Checking certification database URLs...\n")
    print("=" * 60)

    for cert_name, url in CERT_SOURCES.items():
        print(f"Checking {cert_name:<20} ... ", end="", flush=True)
        status = check_url_health(url)
        results["status_by_source"][cert_name] = status

        if status["accessible"]:
            print(f"✅ OK (Status: {status['status']})")
        else:
            print(f"❌ FAILED")
            results["all_healthy"] = False
            results["broken_links"].append({
                "source": cert_name,
                "url": url,
                "status": status.get("status"),
                "error": status.get("error")
            })

    print("=" * 60)
    return results

def save_monitoring_report(results: Dict, filename: str = "url_monitoring_report.json"):
    """Save monitoring results to JSON file"""
    with open(filename, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n📊 Report saved to {filename}")

def print_summary(results: Dict):
    """Print summary of monitoring results"""
    healthy_count = results["total_sources"] - len(results["broken_links"])

    print(f"\n{'='*60}")
    print(f"✅ Healthy: {healthy_count}/{results['total_sources']}")

    if results["all_healthy"]:
        print("🎉 All certification URLs are accessible!")
    else:
        print(f"⚠️  {len(results['broken_links'])} broken link(s) found:\n")
        for broken in results["broken_links"]:
            print(f"  ❌ {broken['source']}: {broken['url']}")
            print(f"     Error: {broken.get('error', broken.get('status'))}\n")

    print(f"Last checked: {results['checked_at']}")
    print(f"{'='*60}\n")

def print_update_instructions(broken_links: List[Dict]):
    """Print instructions for updating broken URLs"""
    if not broken_links:
        return

    print("📝 UPDATE INSTRUCTIONS:")
    print("=" * 60)
    print("If any URLs have changed, update them in app.py:\n")
    print("CERT_SOURCES = {")

    for cert_name, url in CERT_SOURCES.items():
        is_broken = any(b["source"] == cert_name for b in broken_links)
        marker = "❌" if is_broken else "✅"
        print(f'    "{cert_name}": "{url}",  {marker}')

    print("}\n")
    print("After updating, re-run this script to verify.")
    print("=" * 60 + "\n")

def main():
    """Run the monitoring script"""
    print("\n🚀 Certification URL Monitoring\n")

    # Run monitoring
    results = monitor_all_sources()

    # Save report
    save_monitoring_report(results)

    # Print summary
    print_summary(results)

    # Print update instructions if needed
    if results["broken_links"]:
        print_update_instructions(results["broken_links"])

if __name__ == "__main__":
    main()
