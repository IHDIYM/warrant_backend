#!/usr/bin/env python3
"""
Test script to check all APIs on the hosted backend
"""

import requests
import json
import time

# Base URL for the hosted backend
BASE_URL = "https://warrant-backend.onrender.com"

def test_health_check():
    """Test the health check endpoint"""
    print("🔍 Testing Health Check...")
    try:
        response = requests.get(f"{BASE_URL}/", timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_query_api():
    """Test the query API endpoint"""
    print("\n🔍 Testing Query API...")
    try:
        data = {
            "prompt": "What are the available models?",
            "userId": "test-user-123",
            "username": "testuser"
        }
        response = requests.post(f"{BASE_URL}/api/query", json=data, timeout=30)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:200]}...")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_create_user():
    """Test the create user API endpoint"""
    print("\n🔍 Testing Create User API...")
    try:
        data = {
            "name": "Test User",
            "email": "test@example.com",
            "whatsapp": "1234567890"
        }
        response = requests.post(f"{BASE_URL}/api/users", json=data, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code in [200, 201, 409]  # 409 means user already exists
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_auth_api():
    """Test the authentication API endpoint"""
    print("\n🔍 Testing Auth API...")
    try:
        data = {
            "email": "tech@warranty.com",
            "whatsapp": "1234567890",
            "isTechnician": True
        }
        response = requests.post(f"{BASE_URL}/api/auth", json=data, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code in [200, 401]  # 401 means invalid credentials
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_chat_history():
    """Test the chat history API endpoint"""
    print("\n🔍 Testing Chat History API...")
    try:
        user_id = "test-user-123"
        response = requests.get(f"{BASE_URL}/api/chat-history/{user_id}", timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code in [200, 404]  # 404 means no chat history
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_purchases_api():
    """Test the purchases API endpoint"""
    print("\n🔍 Testing Purchases API...")
    try:
        user_id = "test-user-123"
        response = requests.get(f"{BASE_URL}/api/purchases/{user_id}", timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return response.status_code in [200, 404]  # 404 means no purchases
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    """Run all API tests"""
    print("🚀 Starting API Tests for Warrant Backend")
    print("=" * 50)
    
    tests = [
        ("Health Check", test_health_check),
        ("Query API", test_query_api),
        ("Create User", test_create_user),
        ("Auth API", test_auth_api),
        ("Chat History", test_chat_history),
        ("Purchases", test_purchases_api)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        success = test_func()
        results.append((test_name, success))
        time.sleep(1)  # Small delay between tests
    
    # Summary
    print("\n" + "="*50)
    print("📊 TEST SUMMARY")
    print("="*50)
    
    passed = 0
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{test_name}: {status}")
        if success:
            passed += 1
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All APIs are working correctly!")
    else:
        print("⚠️  Some APIs have issues. Check the logs above.")

if __name__ == "__main__":
    main() 